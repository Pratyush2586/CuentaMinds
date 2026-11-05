import json
import logging
from datetime import date

import pandas as pd

from closeops.ingest.mapping import load_dataset
from closeops.ingest.profile import profile_dir
from closeops.model import CANONICAL


def _mapping(tmp_path, body):
    m = tmp_path / "m.yaml"
    m.write_text("period_end: 2026-10-31\ntables:\n" + body, encoding="utf-8")
    return m


def test_load_maps_and_normalises(tmp_path):
    (tmp_path / "inv.csv").write_text("Invoice ID;Net;Vendor\nA1;1.000,50;V1\n", encoding="utf-8")
    m = tmp_path / "m.yaml"
    m.write_text("period_end: 2026-10-31\ntables:\n  invoices:\n    files: ['inv.csv']\n"
                 "    columns: {invoice_id: 'Invoice ID', net_amount: Net, vendor_id: Vendor}\n"
                 "    defaults: {currency: EUR}\n")
    ds = load_dataset(tmp_path, m)
    row = ds.table("invoices").iloc[0]
    assert row.net_amount == 1000.5 and row.currency == "EUR"
    assert row.source_file == "inv.csv" and row.source_row == 2
    assert ds.period_end == date(2026, 10, 31)
    assert list(ds.table("invoices").columns[:len(CANONICAL["invoices"])]) == CANONICAL["invoices"]


def test_unmatched_table_skipped(tmp_path, caplog):
    (tmp_path / "inv.csv").write_text("a,b\n1,2\n")
    m = _mapping(tmp_path, "  contracts:\n    files: ['con*.csv']\n    columns: {contract_id: a}\n")
    with caplog.at_level(logging.WARNING, logger="closeops.ingest"):
        ds = load_dataset(tmp_path, m)
    assert not ds.has("contracts")
    assert "contracts" in caplog.text


def test_profile_lists_columns(tmp_path):
    (tmp_path / "inv.csv").write_text("Invoice ID,Net\nA1,5\nA2,6\n")
    (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4")
    out = profile_dir(tmp_path)
    assert "Invoice ID" in out and "inv.csv" in out
    assert "PDF" in out
    assert "candidate key" in out.lower()


def test_ids_stay_strings_and_unmapped_prefixed(tmp_path):
    (tmp_path / "inv.csv").write_text("no,Extra,IBAN,Day\n007,hello,de 12 34,31/10/2026\n,,,\n")
    m = _mapping(tmp_path, "  vendors:\n    files: ['inv.csv']\n    columns: {vendor_id: 'no', iban: IBAN}\n")
    v = load_dataset(tmp_path, m).table("vendors")
    assert v.vendor_id.iloc[0] == "007"
    assert v.iban.iloc[0] == "DE1234"
    assert v.x_Extra.iloc[0] == "hello"
    assert pd.isna(v.name.iloc[0])  # unmapped canonical column filled with missing


def test_dates_and_tab_delimiter_and_concat(tmp_path):
    (tmp_path / "a1.csv").write_text("id\tdate\nX\t31/10/2026\n")
    (tmp_path / "a2.csv").write_text("id\tdate\nY\t2026-09-30\n")
    m = _mapping(tmp_path, "  fx_rates:\n    files: ['a*.csv']\n    columns: {currency: id, date: date}\n")
    fx = load_dataset(tmp_path, m).table("fx_rates")
    assert list(fx.date) == [date(2026, 10, 31), date(2026, 9, 30)]
    assert list(fx.source_file) == ["a1.csv", "a2.csv"]


def test_xlsx_sheet_and_row_numbers(tmp_path):
    pd.DataFrame({"Acct": ["4000", "5000"], "Nm": ["Rev", "Cost"]}).to_excel(
        tmp_path / "coa.xlsx", sheet_name="Chart", index=False)
    m = _mapping(tmp_path, "  accounts:\n    files: ['coa.xlsx']\n    sheet: Chart\n"
                           "    columns: {account: Acct, name: Nm}\n")
    a = load_dataset(tmp_path, m).table("accounts")
    assert list(a.account) == ["4000", "5000"]
    assert list(a.source_row) == [2, 3]


def test_json_records(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps([{"k": "1", "amt": "2,50"}, {"k": "2", "amt": 3}]))
    m = _mapping(tmp_path, "  bank_transactions:\n    files: ['t.json']\n    columns: {txn_id: k, amount: amt}\n")
    b = load_dataset(tmp_path, m).table("bank_transactions")
    assert list(b.amount) == [2.5, 3.0]
    assert list(b.source_row) == [1, 2]


def test_ragged_csv_row_skipped_keeps_row_numbers(tmp_path):
    (tmp_path / "v.csv").write_text("id,n\n1,a\n2,b,EXTRA\n3,c\n")
    m = _mapping(tmp_path, "  vendors:\n    files: ['v.csv']\n    columns: {vendor_id: id, name: n}\n")
    v = load_dataset(tmp_path, m).table("vendors")
    assert list(v.vendor_id) == ["1", "3"] and list(v.source_row) == [2, 4]


def test_cp1252_csv_loads(tmp_path):
    (tmp_path / "v.csv").write_bytes("id;n\n1;Café\n".encode("cp1252"))
    m = _mapping(tmp_path, "  vendors:\n    files: ['v.csv']\n    columns: {vendor_id: id, name: n}\n")
    assert load_dataset(tmp_path, m).table("vendors").name.iloc[0] == "Café"


def test_bad_files_skipped_good_loaded(tmp_path, caplog):
    (tmp_path / "v_notes.txt").write_text("hello world\n")
    (tmp_path / "v_empty.csv").write_text("")
    (tmp_path / "v_good.csv").write_text("id\n1\n")
    m = _mapping(tmp_path, "  vendors:\n    files: ['v_*']\n    columns: {vendor_id: id}\n")
    with caplog.at_level(logging.WARNING, logger="closeops.ingest"):
        v = load_dataset(tmp_path, m).table("vendors")
    assert list(v.vendor_id) == ["1"]
    assert "v_notes.txt" in caplog.text and "v_empty.csv" in caplog.text


def test_all_files_bad_skips_table(tmp_path):
    (tmp_path / "a.csv").write_text("")
    m = _mapping(tmp_path, "  accounts:\n    files: ['a.csv']\n    sheet: Nope\n    columns: {account: x}\n")
    assert not load_dataset(tmp_path, m).has("accounts")


def test_json_ids_not_floated(tmp_path):
    (tmp_path / "i.json").write_text(json.dumps([{"invoice_id": 1, "x": 5}, {"invoice_id": 2}]))
    m = _mapping(tmp_path, "  invoices:\n    files: ['i.json']\n    columns: {invoice_id: invoice_id}\n")
    assert list(load_dataset(tmp_path, m).table("invoices").invoice_id) == ["1", "2"]


def test_terms_and_received_qty_parse_as_numbers(tmp_path):
    (tmp_path / "c.csv").write_text("id,terms\nC1,Net 30\nC2,14\nC3,soon\n", encoding="utf-8")
    (tmp_path / "g.csv").write_text("gr,po,q\nG1,P1,3 pcs\nG2,P1,junk\n", encoding="utf-8")
    m = _mapping(tmp_path, "  contracts:\n    files: ['c.csv']\n"
                           "    columns: {contract_id: id, payment_terms_days: terms}\n"
                           "  goods_receipts:\n    files: ['g.csv']\n"
                           "    columns: {gr_id: gr, po_id: po, qty_received: q}\n")
    ds = load_dataset(tmp_path, m)
    terms = list(ds.table("contracts")["payment_terms_days"])
    assert terms[:2] == [30.0, 14.0] and pd.isna(terms[2])
    q = list(ds.table("goods_receipts")["qty_received"])
    assert q[0] == 3.0 and pd.isna(q[1])
