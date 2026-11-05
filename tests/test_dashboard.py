import json
import shutil
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from closeops.evidence import EvidenceLog  # noqa: E402
from closeops.pipeline import run_close  # noqa: E402
from closeops.synth.generate import generate  # noqa: E402
from dashboard.data import (latest_run, load_run, reconciled_pct,  # noqa: E402
                            record_decision, source_rows)


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    base = tmp_path_factory.mktemp("dash")
    raw = generate(base / "raw", seed=7, n_invoices=200)
    res = run_close(raw, ROOT / "config" / "mappings.synth.yaml",
                    ROOT / "config" / "policy.yaml", base / "results")
    return raw, res.run_dir


@pytest.fixture
def run_copy(small, tmp_path):
    """Fresh copy of the run so decision tests do not leak into each other."""
    raw, run_dir = small
    dst = tmp_path / run_dir.name
    shutil.copytree(run_dir, dst)
    return raw, dst


def test_load_run_and_latest(small):
    _, run_dir = small
    run = load_run(run_dir)
    assert not run["findings"].empty and "display_tier" in run["findings"].columns
    assert run["evidence_ok"] is True and run["report"].startswith("# Close report")
    assert latest_run(run_dir.parent) == run_dir
    assert latest_run(run_dir.parent / "missing") is None
    assert 0 < reconciled_pct(run) <= 100


def test_source_rows_match_refs(small):
    raw, run_dir = small
    f = load_run(run_dir)["findings"]
    row = next(r for r in f.itertuples() if r.source_refs)
    refs = list(row.source_refs) + ["+5 more", "garbage"]
    df = source_rows(raw, refs)
    real = {r for r in refs if r[0] != "+" and r != "garbage"}
    assert len(df) == len(real)
    assert set(df["source_file"]) == {r.rpartition(":")[0] for r in real}
    assert source_rows(raw, ["+3 more"]).empty


def test_record_decision_keeps_chain_valid(run_copy):
    _, run_dir = run_copy
    f = load_run(run_dir)["findings"]
    fid = f[f["tier"] == "HOLD"]["finding_id"].iloc[0]
    assert record_decision(run_dir, fid, "approve") == "APPROVED"
    last = json.loads((run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["actor"] == "human" and last["action"] == "approve"
    assert last["inputs"] == [fid] and last["output"]["previous_tier"] == "HOLD"
    assert last["tier"] == "AUTO_NOTE"
    assert load_run(run_dir)["findings"].set_index("finding_id").loc[fid, "display_tier"] == "APPROVED"
    assert record_decision(run_dir, fid, "reject") == "REJECTED"
    assert load_run(run_dir)["decisions"] == {fid: "reject"}
    assert EvidenceLog.verify(run_dir / "evidence.jsonl")
    with pytest.raises(ValueError):
        record_decision(run_dir, fid, "maybe")


def _app(run_dir):
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60)
    at.session_state["run_dir"] = str(run_dir)
    return at.run()


def test_app_renders_hold_count(run_copy):
    _, run_dir = run_copy
    at = _app(run_dir)
    assert not at.exception
    hold = int((load_run(run_dir)["findings"]["tier"] == "HOLD").sum())
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["HOLD findings"] == str(hold)
    assert metrics["Evidence chain"].startswith("verified")


def test_app_approve_logs_human_event(run_copy):
    _, run_dir = run_copy
    at = _app(run_dir)
    btn = next(b for b in at.button if b.key and b.key.startswith("approve_"))
    btn.click().run()
    assert not at.exception
    events = [json.loads(ln) for ln in
              (run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["actor"] == "human" and e["action"] == "approve" for e in events)
    assert EvidenceLog.verify(run_dir / "evidence.jsonl")
