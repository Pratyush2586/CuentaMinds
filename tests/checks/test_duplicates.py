import json

from closeops.checks.duplicates import check_duplicates
from helpers import inv, txn


def _pairs(findings):
    return [(f.check, f.record_ids) for f in findings]


def test_exact_duplicate_flags_later(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "INV-001", "2026-10-01", 121.0),
                           inv("I2", "V1", "inv 1", "2026-10-05", 121.0)])
    f = check_duplicates(ds, policy)
    assert _pairs(f) == [("dup_exact", ["I2", "I1"])]
    assert f[0].confidence == 0.99 and f[0].severity == "high"
    assert f[0].record_type == "invoice" and f[0].category == "duplicate"
    assert f[0].amount_eur == 121.0


def test_same_number_different_vendor_not_duplicate(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "100", "2026-10-01", 50.0),
                           inv("I2", "V2", "100", "2026-10-01", 50.0)])
    assert check_duplicates(ds, policy) == []


def test_credit_note_not_duplicate(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "7", "2026-10-01", 100.0),
                           inv("I2", "V1", "7", "2026-10-02", -100.0)])
    assert check_duplicates(ds, policy) == []


def test_near_duplicate_within_window(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "INV-77", "2026-10-01", 300.0),
                           inv("I2", "V1", "INV-77A", "2026-10-04", 300.0)])
    f = check_duplicates(ds, policy)
    assert _pairs(f) == [("dup_near", ["I2", "I1"])]
    assert f[0].confidence == 0.8 and f[0].severity == "medium"


def test_near_duplicate_outside_window_or_amount(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "INV-77", "2026-10-01", 300.0),
                           inv("I2", "V1", "INV-77A", "2026-10-20", 300.0),
                           inv("I3", "V1", "INV-77B", "2026-10-02", 305.0)])
    assert check_duplicates(ds, policy) == []


def test_near_duplicate_matches_vendor_by_name(make_ds, policy):
    ds = make_ds(
        vendors=[{"vendor_id": "V1", "name": "Acme GmbH"}, {"vendor_id": "V2", "name": "ACME, GmbH"}],
        invoices=[inv("I1", "V1", "INV-77", "2026-10-01", 300.0),
                  inv("I2", "V2", "INV-77A", "2026-10-02", 300.0)])
    assert _pairs(check_duplicates(ds, policy)) == [("dup_near", ["I2", "I1"])]


def test_cross_entity_replaces_exact(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "55", "2026-10-01", 80.0),
                           inv("I2", "V1", "55", "2026-10-02", 80.0, entity="E2")])
    f = check_duplicates(ds, policy)
    assert _pairs(f) == [("dup_cross_entity", ["I2", "I1"])]
    assert f[0].entity == "E2" and f[0].confidence == 0.9 and f[0].severity == "high"


def test_cluster_pairs_each_later_copy_with_earliest(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "9", "2026-10-01", 10.0),
                           inv("I2", "V1", "9", "2026-10-02", 10.0),
                           inv("I3", "V1", "9", "2026-10-03", 10.0)])
    assert _pairs(check_duplicates(ds, policy)) == [("dup_exact", ["I2", "I1"]),
                                                    ("dup_exact", ["I3", "I1"])]


def test_duplicate_payment(make_ds, policy):
    ds = make_ds(bank_transactions=[txn("T1", "2026-10-01", -500.0, "DE89370400440532013000"),
                                    txn("T2", "2026-10-02", -500.0, "de89 3704 0044 0532 0130 00"),
                                    txn("T3", "2026-10-02", 500.0, "DE89370400440532013000"),
                                    txn("T4", "2026-10-20", -500.0, "DE89370400440532013000")])
    f = check_duplicates(ds, policy)
    assert _pairs(f) == [("dup_payment", ["T2", "T1"])]
    assert f[0].record_type == "bank_transaction" and f[0].confidence == 0.85
    assert f[0].amount_eur == 500.0


def test_evidence_is_json_plain_and_output_sorted(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "A1", "2026-10-01", 5.0), inv("I2", "V1", "A1", "2026-10-02", 5.0),
                           inv("I3", "V2", "B1", "2026-10-01", 6.0), inv("I4", "V2", "B1", "2026-10-02", 6.0)],
                  bank_transactions=[txn("T1", "2026-10-01", -5.0, "X1"), txn("T2", "2026-10-01", -5.0, "X1")])
    f = check_duplicates(ds, policy)
    json.dumps([x.to_dict() for x in f])
    assert [x.finding_id for x in f] == sorted(x.finding_id for x in f)
    assert len(f) == 3


def test_no_tables_returns_empty(make_ds, policy):
    assert check_duplicates(make_ds(), policy) == []


def test_null_vendor_ids_are_not_the_same_vendor(make_ds, policy):
    from closeops.checks.duplicates import check_duplicates
    ds = make_ds(invoices=[inv("I1", None, "RE-1", "2026-10-01", 100.0),
                           inv("I2", None, "RE-1", "2026-10-02", 100.0)])
    assert check_duplicates(ds, policy) == []
