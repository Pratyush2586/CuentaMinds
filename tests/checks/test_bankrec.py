import json
from datetime import date

from closeops.checks.bankrec import reconcile_bank
from helpers import inv, txn

COLS = ["txn_id", "matched_ids", "method", "confidence"]


def V(vid, iban, name=None, entity="E1"):
    return {"vendor_id": vid, "entity": entity, "name": name or f"Vendor {vid}",
            "country": "DE", "vat_id": None, "iban": iban}


def _rows(m):
    assert list(m.columns) == COLS
    return [(r.txn_id, r.matched_ids, r.method, r.confidence) for r in m.itertuples()]


def test_reference_match_ignores_counterparty(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 invoices=[inv("I1", "V1", "INV-00123", "2026-10-01", 500.0)],
                 bank_transactions=[txn("T1", "2026-10-20", -500.0, "FR999",
                                        reference="Payment inv 123 thanks")])
    m, f = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1"], "reference", 0.99)]
    assert f == []


def test_window_match_three_days_apart_without_vendors(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "A1", "2026-10-01", 250.0)],
                 bank_transactions=[txn("T1", "2026-10-04", -250.0, "DE111")])
    m, f = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1"], "window", 0.8)]
    assert f == []


def test_exact_match_payment_40_days_after_invoice(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 invoices=[inv("I1", "V1", "A1", "2026-09-01", 812.4)],
                 bank_transactions=[txn("T1", "2026-10-11", -812.4, "DE111")])
    m, _ = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1"], "exact", 0.95)]


def test_group_match_of_two_invoices(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 invoices=[inv("I1", "V1", "A1", "2026-10-01", 100.1),
                           inv("I2", "V1", "A2", "2026-10-02", 200.2),
                           inv("I3", "V1", "A3", "2026-10-03", 999.0)],
                 bank_transactions=[txn("T1", "2026-10-10", -300.3, "DE111")])
    m, f = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1", "I2"], "group", 0.8)]
    assert f == []


def test_group_match_uses_only_same_vendor(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111"), V("V2", "DE222")],
                 invoices=[inv("I1", "V1", "A1", "2026-10-01", 100.0),
                           inv("I2", "V2", "B1", "2026-10-01", 200.0),
                           inv("I3", "V1", "A2", "2026-10-02", 150.0),
                           inv("I4", "V1", "A3", "2026-10-03", 50.0)],
                 bank_transactions=[txn("T1", "2026-10-10", -300.0, "DE111")])
    m, _ = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1", "I3", "I4"], "group", 0.8)]


def test_fx_match_usd_line_eur_invoice(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "US111")],
                 invoices=[inv("I1", "V1", "A1", "2026-10-01", 1000.0)],
                 bank_transactions=[txn("T1", "2026-10-05", -1085.0, "US111", currency="USD")],
                 fx_rates=[{"date": date(2026, 10, 1), "currency": "USD", "rate_to_eur": 0.9217}])
    m, f = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1"], "fx", 0.75)]
    assert f == []


def test_unmatched_line_becomes_finding(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 invoices=[inv("I1", "V1", "A1", "2026-10-01", 400.0)],
                 bank_transactions=[txn("T1", "2026-10-05", -123.45, "DE111",
                                        counterparty_name="Vendor V1", reference="misc")])
    m, f = reconcile_bank(ds, policy)
    assert list(m.columns) == COLS and m.empty
    assert len(f) == 1
    x = f[0]
    assert (x.check, x.category, x.severity, x.confidence) == ("bank_unmatched", "bankrec",
                                                               "medium", 0.7)
    assert x.record_type == "bank_transaction" and x.record_ids == ["T1"]
    assert x.amount_eur == 123.45
    e = x.evidence
    assert e["amount"] == -123.45 and e["currency"] == "EUR" and e["txn_date"] == "2026-10-05"
    assert e["counterparty_name"] == "Vendor V1" and e["reference"] == "misc"
    assert e["nearest_candidates"] == ["I1"]
    json.dumps(x.to_dict())


def test_invoice_matched_only_once(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 invoices=[inv("I1", "V1", "A1", "2026-10-01", 700.0)],
                 bank_transactions=[txn("T1", "2026-10-03", -700.0, "DE111"),
                                    txn("T2", "2026-10-04", -700.0, "DE111")])
    m, f = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["I1"], "exact", 0.95)]
    assert [(x.check, x.record_ids) for x in f] == [("bank_unmatched", ["T2"])]


def test_gl_pass_matches_incoming_receipt(make_ds, policy):
    ds = make_ds(accounts=[{"account": "1200", "name": "Bank", "type": "Bank"},
                           {"account": "4000", "name": "Revenue", "type": "income"}],
                 gl_entries=[{"je_id": "JE1", "entity": "E1", "posting_date": date(2026, 10, 7),
                              "account": "4000", "debit": 0.0, "credit": 900.0},
                             {"je_id": "JE1", "entity": "E1", "posting_date": date(2026, 10, 7),
                              "account": "1200", "debit": 900.0, "credit": 0.0}],
                 bank_transactions=[txn("T1", "2026-10-05", 900.0, "DE555"),
                                    txn("T2", "2026-10-07", 900.0, "DE555")])
    m, f = reconcile_bank(ds, policy)
    assert _rows(m) == [("T1", ["JE1"], "window", 0.8)]
    assert [x.record_ids for x in f] == [["T2"]]


def test_no_bank_table(make_ds, policy):
    m, f = reconcile_bank(make_ds(), policy)
    assert list(m.columns) == COLS and m.empty and f == []
