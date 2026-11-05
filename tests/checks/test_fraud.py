import json
from datetime import date

from closeops.checks.fraud import check_fraud
from helpers import inv, txn

SOFT = ("round_amount", "weekend_posting")


def V(vid, iban, country="DE", name=None, entity="E1"):
    return {"vendor_id": vid, "entity": entity, "name": name or f"Vendor {vid}",
            "country": country, "vat_id": None, "iban": iban}


def H(vid, iban, changed_at, by="alice"):
    return {"vendor_id": vid, "iban": iban, "changed_at": date.fromisoformat(changed_at),
            "changed_by": by}


def _checks(findings, soft=False):
    return [(f.check, f.record_ids) for f in findings if soft or f.check not in SOFT]


def test_changed_before_payment(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE222")],
                 vendor_bank_history=[H("V1", "DE111", "2026-01-01"), H("V1", "DE222", "2026-10-01", "bob")],
                 bank_transactions=[txn("T1", "2026-10-10", -1234.5, "DE222")])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("iban_changed_before_payment", ["T1"])]
    x = f[0]
    assert x.category == "fraud" and x.severity == "high" and x.confidence == 0.9
    assert x.record_type == "bank_transaction" and x.amount_eur == 1234.5
    e = x.evidence
    assert e["old_iban"] == "DE111" and e["new_iban"] == "DE222"
    assert e["changed_by"] == "bob" and e["days_between"] == 9
    assert e["changed_at"] == "2026-10-01"
    json.dumps(x.to_dict())


def test_old_iban_falls_back_to_vendor_master(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 vendor_bank_history=[H("V1", "DE222", "2026-10-01")],
                 bank_transactions=[txn("T1", "2026-10-02", -10, "DE222")])
    f = check_fraud(ds, policy)
    assert f[0].evidence["old_iban"] == "DE111"


def test_change_45_days_before_not_flagged(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE222")],
                 vendor_bank_history=[H("V1", "DE222", "2026-09-01")],
                 bank_transactions=[txn("T1", "2026-10-16", -100, "DE222")])
    assert _checks(check_fraud(ds, policy)) == []


def test_payment_before_change_not_flagged(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE222")],
                 vendor_bank_history=[H("V1", "DE222", "2026-10-10")],
                 bank_transactions=[txn("T1", "2026-10-05", -100, "DE222")])
    assert _checks(check_fraud(ds, policy)) == []


def test_not_in_master(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 bank_transactions=[txn("T1", "2026-10-10", -99, "DE999")])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("iban_not_in_master", ["T1"])]
    assert f[0].severity == "high" and f[0].confidence == 0.85


def test_historic_iban_is_in_master(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE222")],
                 vendor_bank_history=[H("V1", "DE111", "2025-01-01")],
                 bank_transactions=[txn("T1", "2026-10-10", -99, "DE111")])
    assert _checks(check_fraud(ds, policy)) == []


def test_incoming_and_null_iban_never_flagged(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111")],
                 vendor_bank_history=[H("V1", "DE222", "2026-10-01")],
                 bank_transactions=[txn("T1", "2026-10-02", 500, "DE222"),
                                    txn("T2", "2026-10-02", 700, "XX999"),
                                    txn("T3", "2026-10-02", -700, None)])
    assert _checks(check_fraud(ds, policy)) == []


def test_not_in_master_skipped_without_vendors(make_ds, policy):
    ds = make_ds(bank_transactions=[txn("T1", "2026-10-10", -99, "DE999")])
    assert _checks(check_fraud(ds, policy)) == []


def test_country_mismatch_by_iban(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "FR111", country="DE")],
                 bank_transactions=[txn("T1", "2026-10-10", -99, "FR111")])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("iban_country_mismatch", ["T1"])]
    assert f[0].severity == "medium" and f[0].confidence == 0.7
    assert f[0].evidence["iban_country"] == "FR" and f[0].evidence["vendor_country"] == "DE"


def test_name_linked_unknown_iban_also_triggers_country(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111", country="DE", name="Acme GmbH")],
                 bank_transactions=[txn("T1", "2026-10-10", -99, "FR777", counterparty_name="ACME GmbH")])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("iban_not_in_master", ["T1"])]
    assert f[0].evidence["also_triggered"] == ["iban_country_mismatch"]


def test_precedence_and_also_triggered(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "FR222", country="DE")],
                 vendor_bank_history=[H("V1", "FR222", "2026-10-01")],
                 bank_transactions=[txn("T1", "2026-10-10", -100, "FR222")])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("iban_changed_before_payment", ["T1"])]
    assert f[0].evidence["also_triggered"] == ["iban_country_mismatch"]


def test_shared_iban(make_ds, policy):
    ds = make_ds(vendors=[V("V2", "DE111"), V("V1", "DE111", entity="E2"), V("V3", "DE333")])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("shared_iban", ["V1", "V2"])]
    assert f[0].record_type == "vendor" and f[0].entity == "E2"
    assert f[0].severity == "high" and f[0].confidence == 0.85
    assert f[0].evidence["iban"] == "DE111"


def test_split_invoices(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "A", "2026-10-01", 6000),
                           inv("I3", "V1", "C", "2026-10-12", 5000),
                           inv("I2", "V1", "B", "2026-10-06", 5000),
                           inv("I4", "V1", "D", "2026-10-25", 9999),
                           inv("I5", "V2", "E", "2026-10-01", 6000)])
    f = check_fraud(ds, policy)
    assert _checks(f) == [("split_invoices", ["I3", "I2", "I1"])]
    assert f[0].record_type == "invoice" and f[0].severity == "medium" and f[0].confidence == 0.7
    assert f[0].amount_eur == 16000


def test_split_negatives(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "A", "2026-10-01", 6000),
                           inv("I2", "V1", "B", "2026-10-20", 6000),  # too far apart
                           inv("I3", "V2", "C", "2026-10-01", 12000),  # above limit itself
                           inv("I4", "V2", "D", "2026-10-02", 100),
                           inv("I5", "V3", "E", "2026-10-01", 4000),  # sum below limit
                           inv("I6", "V3", "F", "2026-10-02", 4000)])
    assert _checks(check_fraud(ds, policy)) == []


def test_soft_signals(make_ds, policy):
    ds = make_ds(bank_transactions=[txn("T1", "2026-10-09", -7000, "DE1"),
                                    txn("T2", "2026-10-09", -7500, "DE1"),
                                    txn("T3", "2026-10-09", 7000, "DE1"),
                                    txn("T4", "2026-10-09", -3000, "DE1")],
                 gl_entries=[{"je_id": "J1", "entity": "E1", "posting_date": date(2026, 10, 10),
                              "account": "1", "debit": 5.0, "credit": 0.0},
                             {"je_id": "J1", "entity": "E1", "posting_date": date(2026, 10, 10),
                              "account": "2", "debit": 0.0, "credit": 5.0},
                             {"je_id": "J2", "entity": "E1", "posting_date": date(2026, 10, 9),
                              "account": "1", "debit": 5.0, "credit": 0.0}])
    f = check_fraud(ds, policy)
    assert _checks(f, soft=True) == [("round_amount", ["T1"]), ("weekend_posting", ["J1"])]
    assert all(x.severity == "low" and x.confidence == 0.4 for x in f)
    assert f[1].record_type == "gl_entry"


def test_output_sorted(make_ds, policy):
    ds = make_ds(vendors=[V("V1", "DE111"), V("V2", "DE111")],
                 bank_transactions=[txn("T2", "2026-10-10", -99, "DE9"), txn("T1", "2026-10-10", -99, "DE8")])
    ids = [x.finding_id for x in check_fraud(ds, policy)]
    assert ids == sorted(ids)
