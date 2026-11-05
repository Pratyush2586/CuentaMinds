from pathlib import Path

import pandas as pd

from closeops.journal import propose_journal_entries, propose_payments
from closeops.model import Finding, Tier
from closeops.pipeline import run_close
from closeops.synth.generate import generate
from tests.helpers import inv, inv_tax, txn

ROOT = Path(__file__).resolve().parent.parent


def _f(check, ids, category="duplicate", record_type="invoice", evidence=None, tier=Tier.AUTO,
       severity="medium"):
    return Finding(check=check, category=category, severity=severity, entity="E1",
                   record_type=record_type, record_ids=ids, evidence=evidence or {},
                   confidence=0.95, suggested_action="", tier=tier)


def _balanced(je):
    for _, g in je.groupby("je_id"):
        assert round(g["debit"].sum() - g["credit"].sum(), 2) == 0


def _lines(je, je_id):
    g = je[je.je_id == je_id]
    return {r.account: (r.debit, r.credit) for r in g.itertuples()}


def test_duplicate_reversal(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", 100.0, 0.19, 19.0, 119.0), inv_tax("I2", 100.0, 0.19, 19.0, 119.0)])
    je = propose_journal_entries(ds, [_f("dup_exact", ["I2", "I1"])], policy)
    _balanced(je)
    assert _lines(je, "ADJ-0001") == {"4000": (119.0, 0.0), "6000": (0.0, 100.0), "4720": (0.0, 19.0)}
    assert set(je["date"]) == {ds.period_end} and set(je["tier"]) == {"AUTO"}
    assert set(je["reason"]) == {"dup_exact"} and set(je["currency"]) == {"EUR"}


def test_duplicate_zero_tax_omits_line(make_ds, policy):
    ds = make_ds(invoices=[inv("I2", "V1", "N", "2026-10-01", 50.0)])
    je = propose_journal_entries(ds, [_f("dup_near", ["I2"])], policy)
    assert _lines(je, "ADJ-0001") == {"4000": (50.0, 0.0), "6000": (0.0, 50.0)}


def test_tax_amount_both_directions(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "N", "2026-10-01", 100.0)])
    under = _f("tax_amount", ["I1"], "tax", evidence={"expected_tax": 19.0, "booked_tax": 9.0})
    over = _f("tax_amount", ["I2"], "tax", evidence={"expected_tax": 9.0, "booked_tax": 19.0})
    je = propose_journal_entries(ds, [over, under], policy)
    _balanced(je)
    assert _lines(je, "ADJ-0001") == {"4720": (10.0, 0.0), "4000": (0.0, 10.0)}  # I1 sorts first
    assert _lines(je, "ADJ-0002") == {"4000": (10.0, 0.0), "4720": (0.0, 10.0)}


def test_cutoff_accrual_and_deferral(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "N", "2026-11-02", 100.0, currency="USD")])
    acc = _f("cutoff", ["I1"], "gl", evidence={"kind": "accrual", "net_amount": 100.0})
    dfr = _f("cutoff", ["I2"], "gl", evidence={"kind": "deferral", "net_amount": 40.0})
    je = propose_journal_entries(ds, [acc, dfr], policy)
    _balanced(je)
    assert _lines(je, "ADJ-0001") == {"6000": (100.0, 0.0), "4110": (0.0, 100.0)}
    assert _lines(je, "ADJ-0002") == {"4800": (40.0, 0.0), "6000": (0.0, 40.0)}
    assert je[je.je_id == "ADJ-0001"]["currency"].iloc[0] == "USD"


def test_bank_unmatched_and_null_amount(make_ds, policy):
    ds = make_ds(bank_transactions=[txn("T1", "2026-10-05", -75.5, "DE1"),
                                    txn("T2", "2026-10-06", 20.0, "DE1"),
                                    txn("T3", "2026-10-06", None, "DE1")])
    fs = [_f("bank_unmatched", [t], "bankrec", "bank_transaction") for t in ("T1", "T2", "T3")]
    je = propose_journal_entries(ds, fs, policy)
    _balanced(je)
    assert _lines(je, "ADJ-0001") == {"5550": (75.5, 0.0), "5720": (0.0, 75.5)}
    assert _lines(je, "ADJ-0002") == {"5720": (20.0, 0.0), "5550": (0.0, 20.0)}
    assert je["je_id"].nunique() == 2


def test_other_findings_make_no_je(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "N", "2026-10-01", 10.0)])
    assert propose_journal_entries(ds, [_f("round_amount", ["I1"], "fraud")], policy).empty


def _pay_ds(make_ds, **kw):
    vendors = [{"vendor_id": "V1", "entity": "E1", "iban": "DE1"},
               {"vendor_id": "V2", "entity": "E1", "iban": "DE2"}]
    invoices = [inv("A", "V1", "1", "2026-10-01", 100.0, status="Open"),
                inv("B", "V2", "2", "2026-10-01", 100.0, status="open"),
                inv("C", "V1", "3", "2026-10-01", 100.0, status="paid"),
                inv("D", "V1", "4", "2026-10-01", -100.0, status="open")]
    return make_ds(vendors=vendors, invoices=invoices, **kw)


def test_clean_open_invoice_is_auto(make_ds, policy):
    ds = _pay_ds(make_ds, contracts=[{"contract_id": "C1", "vendor_id": "V1", "valid_from": None,
                                      "valid_to": None, "payment_terms_days": 45}])
    p = propose_payments(ds, [], policy).set_index("invoice_id")
    assert list(p.index) == ["A", "B"]  # paid and credit note excluded
    assert p.loc["A", "tier"] == "AUTO" and p.loc["A", "block_reason"] == ""
    assert str(p.loc["A", "due_date"]) == "2026-11-15" and str(p.loc["B", "due_date"]) == "2026-10-31"
    assert p.loc["A", "iban"] == "DE1" and p.loc["A", "amount"] == 100.0


def test_iban_changed_recently_is_hold(make_ds, policy):
    hist = [{"vendor_id": "V1", "iban": "DE1", "changed_at": pd.Timestamp("2026-10-21").date()}]
    p = propose_payments(_pay_ds(make_ds, vendor_bank_history=hist), [], policy).set_index("invoice_id")
    assert p.loc["A", "tier"] == "HOLD" and p.loc["A", "block_reason"] == "iban_changed"
    assert p.loc["B", "tier"] == "AUTO"


def test_old_iban_change_outside_window_is_not_held(make_ds, policy):
    hist = [{"vendor_id": "V1", "iban": "DE1", "changed_at": pd.Timestamp("2026-09-01").date()}]
    p = propose_payments(_pay_ds(make_ds, vendor_bank_history=hist), [], policy).set_index("invoice_id")
    assert p.loc["A", "tier"] == "AUTO"


def test_duplicate_fraud_match_holds(make_ds, policy):
    fs = [_f("dup_exact", ["A", "X"]),
          _f("iban_not_in_master", ["T9"], "fraud", "bank_transaction", {"vendor_id": "V2"},
             severity="high"),
          _f("po_price_mismatch", ["B"], "match")]
    p = propose_payments(_pay_ds(make_ds), fs, policy).set_index("invoice_id")
    assert p.loc["A", "block_reason"] == "dup_exact"
    assert p.loc["B", "block_reason"] == "iban_not_in_master"  # vendor-level fraud beats match
    assert (p["tier"] == "HOLD").all()


def test_no_iban_and_materiality(make_ds, policy):
    ds = make_ds(vendors=[{"vendor_id": "V1", "entity": "E1", "iban": None},
                          {"vendor_id": "V2", "entity": "E1", "iban": "DE2"}],
                 invoices=[inv("A", "V1", "1", "2026-10-01", 10.0, status="open"),
                           inv("B", "V2", "2", "2026-10-01", 9000.0, status="open"),
                           inv("C", "V2", "3", "2026-10-01", 10.0, status="open")])
    p = propose_payments(ds, [], policy).set_index("invoice_id")
    assert p.loc["A", "block_reason"] == "no_iban"
    assert p.loc["B", "block_reason"] == "materiality"
    assert p.loc["C", "tier"] == "AUTO"


def test_null_status_uses_bank_matches(make_ds, policy):
    ds = make_ds(vendors=[{"vendor_id": "V1", "entity": "E1", "iban": "DE1"}],
                 invoices=[inv("A", "V1", "1", "2026-10-01", 10.0),
                           inv("B", "V1", "2", "2026-10-01", 10.0)])
    bm = pd.DataFrame([{"txn_id": "T", "matched_ids": ["A"], "method": "x", "confidence": 1.0}])
    assert list(propose_payments(ds, [], policy, bm)["invoice_id"]) == ["B"]


def test_seed7_pipeline_outputs(tmp_path):
    raw = generate(tmp_path / "raw", seed=7)
    res = run_close(raw, ROOT / "config" / "mappings.synth.yaml", ROOT / "config" / "policy.yaml",
                    tmp_path / "r")
    je = res.journal_entries
    ids = list(dict.fromkeys(je["je_id"]))
    assert ids and ids == [f"ADJ-{i:04d}" for i in range(1, len(ids) + 1)]
    _balanced(je)
    assert (res.run_dir / "journal_entries.csv").exists()
    assert len(pd.read_csv(res.run_dir / "payment_proposals.csv")) == len(res.payments)
    p = res.payments
    trap_vendors = {f.evidence["vendor_id"] for f in res.findings
                    if f.check == "iban_changed_before_payment" and f.evidence.get("vendor_id")}
    assert trap_vendors
    assert p[p.vendor_id.isin(trap_vendors) & (p.tier != "HOLD")].empty
    assert set(p.tier) <= {"AUTO", "HOLD"} and (p[p.tier == "HOLD"].block_reason != "").all()


def test_vendor_wide_hold_only_for_high_fraud(make_ds, policy):
    ds = _pay_ds(make_ds)
    ds1 = make_ds(vendors=[{"vendor_id": "V1", "entity": "E1", "iban": "DE1"}],
                  invoices=[inv("A", "V1", "1", "2026-10-01", 100.0, status="open"),
                            inv("E", "V1", "5", "2026-10-02", 70.0, status="open")])
    dup = propose_payments(ds1, [_f("dup_exact", ["A", "X"], evidence={"vendor_id": "V1"})],
                           policy).set_index("invoice_id")
    assert dup.loc["A", "tier"] == "HOLD" and dup.loc["A", "block_reason"] == "dup_exact"
    assert dup.loc["E", "tier"] == "AUTO"  # unrelated invoice of the same vendor
    med = _f("iban_country_mismatch", ["T9"], "fraud", "bank_transaction", {"vendor_id": "V2"})
    assert (propose_payments(ds, [med], policy)["tier"] == "AUTO").all()
    hi = _f("iban_changed_before_payment", ["T9"], "fraud", "bank_transaction",
            {"vendor_id": "V2"}, severity="high")
    p = propose_payments(ds, [hi], policy).set_index("invoice_id")
    assert p.loc["B", "tier"] == "HOLD" and p.loc["A", "tier"] == "AUTO"
