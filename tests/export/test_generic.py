import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from closeops.checks.fraud import SOFT_CHECKS
from closeops.export.generic import write_results
from closeops.pipeline import run_close
from closeops.synth.generate import generate

ROOT = Path(__file__).resolve().parent.parent.parent
MAPPING = ROOT / "config" / "mappings.synth.yaml"
POLICY = ROOT / "config" / "policy.yaml"

FILES = {"findings.csv", "findings.json", "journal_entries.csv", "payment_proposals.csv",
         "bank_reconciliation.csv", "match_status.csv", "trial_balance.csv",
         "close_report.md", "policy_used.yaml"}
COLUMNS = ["finding_id", "check", "category", "severity", "entity", "record_type",
           "primary_record_id", "record_ids", "confidence", "amount_eur", "tier",
           "requires_human_review", "explanation", "resolved_by", "source_refs"]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    base = tmp_path_factory.mktemp("export")
    raw = generate(base / "raw", seed=7, n_invoices=200)
    return run_close(raw, MAPPING, POLICY, base / "results")


def _policy(soft: bool) -> dict:
    p = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    p["fraud"]["export_soft_signals"] = soft
    return p


def _exported(run, soft: bool):
    return [f for f in run.findings if soft or f.check not in SOFT_CHECKS]


def test_all_files_written(run):
    paths = write_results(run, _policy(False))
    assert {p.name for p in paths} == FILES
    assert all(p.parent == run.run_dir and p.exists() for p in paths)


def test_findings_csv_traceable(run):
    write_results(run, _policy(False))
    df = pd.read_csv(run.run_dir / "findings.csv", dtype=str, keep_default_na=False)
    assert list(df.columns) == COLUMNS
    assert len(df) == len(_exported(run, False))
    assert (df["source_refs"] != "").all()
    assert (df["primary_record_id"] != "").all()
    assert df["source_refs"].str.contains(r"^[^;]+:\d+", regex=True).all()


def test_soft_signals_only_when_enabled(run):
    soft_present = any(f.check in SOFT_CHECKS for f in run.findings)
    write_results(run, _policy(False))
    off = json.loads((run.run_dir / "findings.json").read_text(encoding="utf-8"))
    assert not {r["check"] for r in off} & SOFT_CHECKS
    write_results(run, _policy(True))
    on = json.loads((run.run_dir / "findings.json").read_text(encoding="utf-8"))
    assert len(on) == len(_exported(run, True))
    assert soft_present == bool({r["check"] for r in on} & SOFT_CHECKS)
    assert isinstance(on[0]["record_ids"], list) and isinstance(on[0]["source_refs"], list)


def test_report_mentions_entities(run):
    write_results(run, _policy(False))
    text = (run.run_dir / "close_report.md").read_text(encoding="utf-8")
    assert run.dataset.period_end.isoformat() in text
    for ent in sorted(run.dataset.table("bank_transactions")["entity"].astype(str).unique()):
        assert ent in text
    assert "HOLD" in text and "reconciled" in text.lower()


def test_policy_copy_and_side_tables(run):
    write_results(run, _policy(False))
    assert yaml.safe_load((run.run_dir / "policy_used.yaml").read_text(encoding="utf-8"))["fraud"]
    br = pd.read_csv(run.run_dir / "bank_reconciliation.csv", dtype=str, keep_default_na=False)
    assert list(br.columns)[:2] == ["txn_id", "matched_ids"]
    assert len(br) == len(run.side_tables["bank_matches"])


def test_refs_capped_and_json_safe(run):
    from closeops.export.generic import _json_default
    import numpy as np
    from datetime import date
    assert _json_default(np.int64(3)) == 3 and _json_default(np.float64(1.5)) == 1.5
    assert _json_default(date(2026, 1, 2)) == "2026-01-02"
    assert _json_default(pd.Timestamp("2026-01-02")) == "2026-01-02T00:00:00"


def test_ref_resolution_and_cap(make_ds):
    from closeops.export.generic import _RefIndex
    from closeops.model import Finding
    gl = [{"je_id": f"J{i}", "entity": "A", "counterparty_entity": "B"} for i in range(25)]
    gl += [{"je_id": "JB", "entity": "B", "counterparty_entity": "A"},
           {"je_id": "JC", "entity": "C"}]
    idx = _RefIndex(make_ds(gl_entries=gl))

    def f(rtype, ids):
        return Finding(check="x", category="gl", severity="low", entity="A", record_type=rtype,
                       record_ids=ids, evidence={}, confidence=1.0, suggested_action="")

    ent = idx.refs(f("entity", ["A"]))
    assert len(ent) == 21 and ent[-1] == "+5 more" and ent[0] == "test:0"
    pair = idx.refs(f("entity_pair", ["A", "B"]))
    assert len(pair) == 21 and pair[-1] == "+6 more"
    assert idx.refs(f("gl_entry", ["JC"])) == ["test:26"]
    assert idx.refs(f("invoice", ["nope"])) == []
