"""End-to-end on an uglified copy of seed 7: messy cells and per-file bank layouts."""
import csv
import io
import json
from pathlib import Path

import pytest
import yaml

from closeops.pipeline import run_close
from closeops.scoring import score
from closeops.synth.generate import generate

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "config" / "policy.yaml"


def _rows(path: Path, delim: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(path.read_text(encoding="utf-8")), delimiter=delim))


def _write(path: Path, rows: list[list[str]], delim: str, preamble: str = "") -> None:
    buf = io.StringIO()
    csv.writer(buf, delimiter=delim, lineterminator="\n").writerows(rows)
    path.write_text(preamble + buf.getvalue(), encoding="utf-8")


def _uglify(raw: Path) -> None:
    # DE01 bank: SAP-style trailing minus on outgoing amounts
    p = raw / "bank_statement_DE01.csv"
    rows = _rows(p, ";")
    amt = rows[0].index("Amount")
    for r in rows[1:]:
        if r[amt].startswith("-"):
            r[amt] = r[amt][1:] + "-"
    _write(p, rows, ";")
    # FR01 bank: two preamble lines before the header
    p = raw / "bank_statement_FR01.csv"
    _write(p, _rows(p, ";"), ";", preamble="Releve de compte FR01\nPeriode: octobre 2026\n")
    # ES01 bank: no entity column (given by per-file defaults)
    p = raw / "bank_statement_ES01.csv"
    rows = _rows(p, ";")
    ent = rows[0].index("Company")
    _write(p, [r[:ent] + r[ent + 1:] for r in rows], ";")
    # invoices: padded entity codes
    p = raw / "invoices.csv"
    rows = _rows(p, ",")
    ent = rows[0].index("Company Code")
    for r in rows[1:]:
        r[ent] = f" {r[ent]} "
    _write(p, rows, ",")
    # contracts: payment terms as text
    p = raw / "contracts.json"
    contracts = json.loads(p.read_text(encoding="utf-8"))
    for c in contracts:
        c["paymentTermsDays"] = f"Net {c['paymentTermsDays']}"
    p.write_text(json.dumps(contracts, indent=2), encoding="utf-8")
    # goods receipts: one unparseable receipt date
    p = raw / "goods_receipts.csv"
    rows = _rows(p, ",")
    rows[1][rows[0].index("Received On")] = "n/a"
    _write(p, rows, ",")


def _mapping(tmp: Path) -> Path:
    cfg = yaml.safe_load((ROOT / "config" / "mappings.synth.yaml").read_text(encoding="utf-8"))
    bank = cfg["tables"]["bank_transactions"]
    no_entity = {k: v for k, v in bank["columns"].items() if k != "entity"}
    cfg["tables"]["bank_transactions"] = [
        {"files": ["bank_statement_DE01.csv"], "columns": bank["columns"]},
        {"files": ["bank_statement_FR01.csv"], "columns": bank["columns"], "skip_rows": 2},
        {"files": ["bank_statement_ES01.csv"], "columns": no_entity, "defaults": {"entity": "ES01"}},
    ]
    m = tmp / "mapping.ugly.yaml"
    m.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return m


@pytest.fixture(scope="module")
def ugly(tmp_path_factory):
    base = tmp_path_factory.mktemp("ugly")
    raw = generate(base / "raw", seed=7)
    _uglify(raw)
    res = run_close(raw, _mapping(base), POLICY, base / "results")
    key = json.loads((raw / "answer_key.json").read_text(encoding="utf-8"))
    return res, score(res.findings, key)


def test_ugly_run_completes_with_notes(ugly):
    res, _ = ugly
    assert not any(n.startswith("error in") for n in res.notes)
    assert any(n.startswith("goods_receipts.receipt_date: 1/") and n.endswith("unparsed")
               for n in res.notes)
    bank = res.dataset.table("bank_transactions")
    assert set(bank["entity"]) == {"DE01", "FR01", "ES01"}
    assert bank["amount"].notna().all()
    assert (bank[bank["entity"] == "DE01"]["amount"] < 0).any()
    assert set(res.dataset.table("invoices")["entity"]) <= {"DE01", "FR01", "ES01"}
    assert res.dataset.table("contracts")["payment_terms_days"].notna().all()
    fr = bank[bank["entity"] == "FR01"]
    assert fr["source_row"].min() == 4  # header on line 3 after the preamble


def test_ugly_scores(ugly):
    _, s = ugly
    assert s["overall"]["recall"] >= 0.85 and s["overall"]["precision"] >= 0.9
