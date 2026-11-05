"""Messy-cell and failing-stage robustness of run_close."""
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

import closeops.pipeline as pipeline
from closeops.journal import PAY_COLUMNS
from closeops.pipeline import run_close
from closeops.synth.generate import generate

ROOT = Path(__file__).resolve().parent.parent
MAPPING = ROOT / "config" / "mappings.synth.yaml"
POLICY = ROOT / "config" / "policy.yaml"


@pytest.fixture(scope="module")
def small_raw(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("robust") / "raw", seed=7, n_invoices=200)


def _copy(src: Path, dst: Path) -> Path:
    dst.mkdir(parents=True)
    for p in src.iterdir():
        (dst / p.name).write_bytes(p.read_bytes())
    return dst


def test_payment_terms_text_and_qty_units(small_raw, tmp_path):
    raw = _copy(small_raw, tmp_path / "raw")
    cp = raw / "contracts.json"
    contracts = json.loads(cp.read_text(encoding="utf-8"))
    for c in contracts:
        c["paymentTermsDays"] = "Net 45"
    cp.write_text(json.dumps(contracts), encoding="utf-8")
    gp = raw / "goods_receipts.csv"
    lines = gp.read_text(encoding="utf-8").splitlines()
    head, rest = lines[0], [ln.rsplit(",", 1) for ln in lines[1:]]
    gp.write_text("\n".join([head] + [f"{a},{q} pcs" for a, q in rest]) + "\n", encoding="utf-8")

    res = run_close(raw, MAPPING, POLICY, tmp_path / "r")
    assert set(res.dataset.table("contracts")["payment_terms_days"]) == {45.0}
    assert res.dataset.table("goods_receipts")["qty_received"].notna().all()
    assert any(f.check == "qty_over_received" for f in res.findings)
    assert not any(n.startswith("error in") for n in res.notes)
    inv = res.dataset.table("invoices").set_index("invoice_id")
    vendors = set(res.dataset.table("contracts")["vendor_id"])
    p = res.payments[res.payments["vendor_id"].isin(vendors) & res.payments["due_date"].notna()]
    assert not p.empty
    for r in p.itertuples():
        assert r.due_date == inv.loc[r.invoice_id, "invoice_date"] + timedelta(days=45)


def test_failing_payment_stage_still_exports(small_raw, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad cell")
    monkeypatch.setattr(pipeline, "propose_payments", boom)
    res = run_close(small_raw, MAPPING, POLICY, tmp_path / "r")
    assert "error in propose_payments: ValueError: bad cell" in res.notes
    assert list(res.payments.columns) == PAY_COLUMNS and res.payments.empty
    out = json.loads((res.run_dir / "findings.json").read_text(encoding="utf-8"))
    assert len(out) == len(res.findings) > 0
    assert len(pd.read_csv(res.run_dir / "journal_entries.csv")) == len(res.journal_entries) > 0


def test_failing_je_stage_still_exports(small_raw, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise KeyError("accounts")
    monkeypatch.setattr(pipeline, "propose_journal_entries", boom)
    res = run_close(small_raw, MAPPING, POLICY, tmp_path / "r")
    assert any(n.startswith("error in propose_journal_entries") for n in res.notes)
    assert res.journal_entries.empty and "je_id" in res.journal_entries.columns
    assert (res.run_dir / "findings.json").exists() and not res.payments.empty
