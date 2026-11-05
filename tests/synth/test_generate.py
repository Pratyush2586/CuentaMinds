import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from closeops.ingest.mapping import load_dataset
from closeops.model import CANONICAL
from closeops.synth.generate import generate

MAPPING = Path(__file__).resolve().parents[2] / "config" / "mappings.synth.yaml"

EXPECTED = {
    "dup_exact": 12, "dup_near": 6, "dup_cross_entity": 2, "dup_payment": 3,
    "tax_math": 4, "tax_amount": 4, "tax_rate": 4, "tax_reverse_charge": 4,
    "iban_changed_before_payment": 4, "iban_not_in_master": 2,
    "split_invoices": 2, "shared_iban": 1,
    "po_price_mismatch": 10, "contract_price_mismatch": 5, "qty_over_received": 5, "no_po": 6,
    "bank_unmatched": 8, "intercompany_mismatch": 2, "cutoff": 6,
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    assert generate(out) == out
    return out


def _hashes(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def test_all_tables_load(generated):
    ds = load_dataset(generated, MAPPING)
    assert sorted(ds.tables) == sorted(CANONICAL)
    for name in CANONICAL:
        assert ds.has(name), name
    inv = ds.table("invoices")
    assert len(inv) >= 600
    assert set(inv.entity) == {"ES01", "DE01", "FR01"}
    assert inv.invoice_date.notna().all() and inv.gross_amount.notna().all()
    bank = ds.table("bank_transactions")
    assert set(bank.source_file) == {f"bank_statement_{e}.csv" for e in ("ES01", "DE01", "FR01")}
    assert "USD" in set(bank.currency)
    assert ds.period_end.isoformat() == "2026-10-31"


def test_answer_key_counts(generated):
    key = json.loads((generated / "answer_key.json").read_text(encoding="utf-8"))
    assert Counter(e["check"] for e in key) == EXPECTED
    assert all(set(e) == {"category", "check", "record_id"} for e in key)
    assert len({(e["check"], e["record_id"]) for e in key}) == len(key)


def test_same_seed_identical_files(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    generate(a, seed=11, n_invoices=200)
    generate(b, seed=11, n_invoices=200)
    generate(c, seed=12, n_invoices=200)
    assert _hashes(a) == _hashes(b)
    assert len(_hashes(a)) == 14
    assert _hashes(a) != _hashes(c)


def test_too_few_invoices_rejected(tmp_path):
    with pytest.raises(ValueError):
        generate(tmp_path, n_invoices=199)


HARD_EXTRA = {"po_price_mismatch": 2, "dup_exact": 1, "dup_near": 1,
              "iban_changed_before_payment": 1}


def test_hard_mode_loads_with_extra_traps_and_is_deterministic(tmp_path):
    a, b, normal = tmp_path / "a", tmp_path / "b", tmp_path / "n"
    generate(a, seed=11, n_invoices=200, hard=True)
    generate(b, seed=11, n_invoices=200, hard=True)
    generate(normal, seed=11, n_invoices=200)
    assert _hashes(a) == _hashes(b)
    assert _hashes(a) != _hashes(normal)
    ds = load_dataset(a, MAPPING)
    assert sorted(ds.tables) == sorted(CANONICAL)
    key = json.loads((a / "answer_key.json").read_text(encoding="utf-8"))
    assert Counter(e["check"] for e in key) == Counter(EXPECTED) + Counter(HARD_EXTRA)
    assert len({(e["check"], e["record_id"]) for e in key}) == len(key)
    # recurring decoy: some vendor bills one gross amount in three different months
    inv = ds.table("invoices")
    months = inv.groupby(["vendor_id", "gross_amount"])["invoice_date"].apply(
        lambda s: len({d.month for d in s}))
    assert (months >= 3).sum() >= 2
