import json
from datetime import date

from closeops.checks.purchasing import check_purchasing, match_status
from helpers import inv


def PO(pid, price=100.0, qty=10, item="Widget", vendor="V1"):
    return {"po_id": pid, "entity": "E1", "vendor_id": vendor, "po_date": date(2026, 9, 1),
            "item": item, "qty": qty, "unit_price": price, "currency": "EUR", "approved_by": "x"}


def GR(gid, pid, qty):
    return {"gr_id": gid, "po_id": pid, "receipt_date": date(2026, 9, 20), "qty_received": qty}


def C(cid, price, item="widget ", vendor="V1", vf=None, vt=None):
    return {"contract_id": cid, "vendor_id": vendor, "item": item, "unit_price": price,
            "currency": "EUR", "valid_from": vf, "valid_to": vt, "payment_terms_days": 30}


def I(iid, po="P1", price=100.0, qty=10, item="Widget", **extra):
    return inv(iid, "V1", f"N-{iid}", "2026-10-01", (price or 0) * qty, po_id=po,
               unit_price=price, qty=qty, item=item, **extra)


def _status(ds, policy):
    df = match_status(ds, policy)
    return dict(zip(df["invoice_id"], df["status"]))


def test_matched(make_ds, policy):
    ds = make_ds(invoices=[I("I1")], purchase_orders=[PO("P1")], goods_receipts=[GR("G1", "P1", 10)])
    assert check_purchasing(ds, policy) == []
    assert _status(ds, policy) == {"I1": "matched"}


def test_price_tolerance(make_ds, policy):
    ds = make_ds(invoices=[I("I1", price=103.0), I("I2", po="P2", price=101.5)],
                 purchase_orders=[PO("P1"), PO("P2")],
                 goods_receipts=[GR("G1", "P1", 10), GR("G2", "P2", 10)])
    f = check_purchasing(ds, policy)
    assert [(x.check, x.record_ids, x.severity) for x in f] == [("po_price_mismatch", ["I1"], "medium")]
    json.dumps(f[0].evidence)
    assert _status(ds, policy) == {"I1": "price_mismatch", "I2": "matched"}


def test_no_goods_receipts_table(make_ds, policy):
    ds = make_ds(invoices=[I("I1")], purchase_orders=[PO("P1")])
    assert check_purchasing(ds, policy) == []
    assert _status(ds, policy) == {"I1": "no_receipt"}


def test_qty_over_received_sums_rows_and_precedence(make_ds, policy):
    ds = make_ds(invoices=[I("I1", price=110.0, qty=10)], purchase_orders=[PO("P1")],
                 goods_receipts=[GR("G1", "P1", 4), GR("G2", "P1", 5)])
    f = check_purchasing(ds, policy)
    assert [(x.check, x.severity) for x in f] == [("qty_over_received", "high")]
    assert f[0].evidence["qty_received"] == 9.0
    assert f[0].evidence["also_triggered"] == ["po_price_mismatch"]
    assert _status(ds, policy) == {"I1": "qty_mismatch"}


def test_no_po(make_ds, policy):
    ds = make_ds(invoices=[I("I1", po=None), I("I2", po="PX")], purchase_orders=[PO("P1")])
    f = check_purchasing(ds, policy)
    assert [(x.check, x.record_ids) for x in f] == [("no_po", ["I1"]), ("no_po", ["I2"])]
    assert _status(ds, policy) == {"I1": "no_po", "I2": "no_po"}


def test_no_po_skipped_without_po_table(make_ds, policy):
    ds = make_ds(invoices=[I("I1", po=None)])
    assert check_purchasing(ds, policy) == []


def test_contract_price(make_ds, policy):
    ds = make_ds(invoices=[I("I1", po=None, price=110.0)],
                 purchase_orders=[PO("P9")],
                 contracts=[C("C1", 100.0, vf=date(2026, 1, 1)),
                            C("C2", 50.0, vf=date(2026, 11, 1))])
    f = check_purchasing(ds, policy)
    assert [(x.check, x.evidence["contract_id"]) for x in f] == [("contract_price_mismatch", "C1")]
    assert f[0].evidence["also_triggered"] == []  # po_id column entirely empty: no_po skipped


def test_contract_within_tolerance_or_expired(make_ds, policy):
    ds = make_ds(invoices=[I("I1", po="P1", price=101.0)], purchase_orders=[PO("P1", price=101.0)],
                 goods_receipts=[GR("G1", "P1", 10)],
                 contracts=[C("C1", 100.0), C("C2", 10.0, vt=date(2026, 9, 30))])
    assert check_purchasing(ds, policy) == []


def test_credit_notes_skipped(make_ds, policy):
    ds = make_ds(invoices=[I("I1", po=None, price=-100.0, qty=1, net_amount=-100.0)],
                 purchase_orders=[PO("P1")])
    assert check_purchasing(ds, policy) == []
    assert _status(ds, policy) == {"I1": "credit_note"}


def test_zero_or_missing_ref_price_skipped(make_ds, policy):
    ds = make_ds(invoices=[I("I1", price=5.0), I("I2", po="P2", price=None)],
                 purchase_orders=[PO("P1", price=0.0), PO("P2")],
                 goods_receipts=[GR("G1", "P1", 10), GR("G2", "P2", 10)])
    assert check_purchasing(ds, policy) == []


def test_no_receipt_rows_for_po_no_qty_finding(make_ds, policy):
    ds = make_ds(invoices=[I("I1")], purchase_orders=[PO("P1"), PO("P2")],
                 goods_receipts=[GR("G1", "P2", 10)])
    assert check_purchasing(ds, policy) == []
    assert _status(ds, policy) == {"I1": "no_receipt"}


def test_partial_receipt_still_fires(make_ds, policy):
    ds = make_ds(invoices=[I("I1")], purchase_orders=[PO("P1")],
                 goods_receipts=[GR("G1", "P1", 3)])
    assert [x.check for x in check_purchasing(ds, policy)] == ["qty_over_received"]


def test_no_po_skipped_when_po_column_empty(make_ds, policy):
    from closeops.checks.registry import run_checks
    ds = make_ds(invoices=[inv("I1", "V1", "N1", "2026-10-01", 100.0),
                           inv("I2", "V1", "N2", "2026-10-02", 50.0)],
                 purchase_orders=[{"po_id": "P1", "vendor_id": "V1", "unit_price": 10.0}])
    assert check_purchasing(ds, policy) == []
    fs, _, notes = run_checks(ds, policy)
    assert not any(f.check == "no_po" for f in fs)
    assert "skipped no_po: po_id column empty" in notes
