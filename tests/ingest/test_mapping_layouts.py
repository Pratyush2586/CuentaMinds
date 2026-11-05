"""Multi-entity layouts: list specs, skip_rows, debit/credit and sign columns, whitespace,
.txt files, unparsed-value notes, date order and cross-entity id handling."""
import logging
from datetime import date

import pandas as pd
from openpyxl import Workbook

from closeops.ingest.mapping import load_dataset
from closeops.ingest.profile import profile_dir


def _mapping(tmp_path, body, head=""):
    m = tmp_path / "m.yaml"
    m.write_text("period_end: 2026-10-31\n" + head + "tables:\n" + body, encoding="utf-8")
    return m


def test_list_of_specs_with_per_spec_defaults(tmp_path):
    (tmp_path / "bank_de.csv").write_text("Id;Betrag\nD1;-10,00\n", encoding="utf-8")
    (tmp_path / "bank_fr.csv").write_text("Ref,Montant,Societe\nF1,20.00,FR01\n", encoding="utf-8")
    m = _mapping(tmp_path, """  bank_transactions:
    - files: ['bank_de.csv']
      columns: {txn_id: Id, amount: Betrag}
      defaults: {entity: DE01, currency: EUR}
    - files: ['bank_fr.csv']
      columns: {txn_id: Ref, amount: Montant, entity: Societe}
""")
    b = load_dataset(tmp_path, m).table("bank_transactions")
    assert list(b.txn_id) == ["D1", "F1"] and list(b.entity) == ["DE01", "FR01"]
    assert list(b.amount) == [-10.0, 20.0]
    assert b.currency.iloc[0] == "EUR" and pd.isna(b.currency.iloc[1])
    assert list(b.source_file) == ["bank_de.csv", "bank_fr.csv"]


def test_per_spec_sheet(tmp_path):
    with pd.ExcelWriter(tmp_path / "coa.xlsx") as xw:
        pd.DataFrame({"A": ["4000"]}).to_excel(xw, sheet_name="DE", index=False)
        pd.DataFrame({"B": ["5000"]}).to_excel(xw, sheet_name="FR", index=False)
    m = _mapping(tmp_path, """  accounts:
    - {files: ['coa.xlsx'], sheet: DE, columns: {account: A}}
    - {files: ['coa.xlsx'], sheet: FR, columns: {account: B}}
""")
    assert list(load_dataset(tmp_path, m).table("accounts").account) == ["4000", "5000"]


def test_skip_rows_csv_and_xlsx(tmp_path):
    (tmp_path / "b.csv").write_text(
        "Kontoauszug DE01\nZeitraum: Oktober 2026\nId;Betrag\nX1;5,00\nX2;6,00\n", encoding="utf-8")
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    for row in (["Statement FR01"], ["generated today"], ["Ref", "Amt"], ["Y1", "7.5"]):
        ws.append(row)
    wb.save(tmp_path / "b.xlsx")
    m = _mapping(tmp_path, """  bank_transactions:
    - {files: ['b.csv'], skip_rows: 2, columns: {txn_id: Id, amount: Betrag}}
    - {files: ['b.xlsx'], sheet: S, skip_rows: 2, columns: {txn_id: Ref, amount: Amt}}
""")
    b = load_dataset(tmp_path, m).table("bank_transactions")
    assert list(b.txn_id) == ["X1", "X2", "Y1"] and list(b.amount) == [5.0, 6.0, 7.5]
    assert list(b.source_row) == [4, 5, 4]  # physical line / sheet row numbers


def test_amount_from_debit_credit(tmp_path):
    (tmp_path / "b.csv").write_text("Id,Soll,Haben\nA,100.00,\nB,,250.50\nC,,\nD,x,\n",
                                    encoding="utf-8")
    m = _mapping(tmp_path, """  bank_transactions:
    files: ['b.csv']
    columns: {txn_id: Id}
    amount_from: {debit: Soll, credit: Haben}
""")
    ds = load_dataset(tmp_path, m)
    b = ds.table("bank_transactions")
    assert list(b.amount[:2]) == [-100.0, 250.5]
    assert pd.isna(b.amount.iloc[2]) and pd.isna(b.amount.iloc[3])
    assert "x_Soll" not in b.columns
    assert "bank_transactions.amount: 1/3 unparsed" in ds.notes


def test_sign_column(tmp_path):
    (tmp_path / "b.csv").write_text("Id,Amt,DC\nA,100.00,D\nB,50.00,C\nC,20.00, dr \nD,5.00,-\n",
                                    encoding="utf-8")
    m = _mapping(tmp_path, """  bank_transactions:
    files: ['b.csv']
    columns: {txn_id: Id, amount: Amt}
    sign_col: DC
    sign_debit_values: [D, DR, "-"]
""")
    amounts = list(load_dataset(tmp_path, m).table("bank_transactions").amount)
    assert amounts == [-100.0, 50.0, -20.0, -5.0]


def test_text_cells_are_stripped(tmp_path):
    (tmp_path / "v.csv").write_text("id,ent,Note\n V1 , DE01 ,  hi \n", encoding="utf-8")
    m = _mapping(tmp_path, "  vendors:\n    files: ['v.csv']\n    columns: {vendor_id: id, entity: ent}\n")
    v = load_dataset(tmp_path, m).table("vendors")
    assert (v.vendor_id.iloc[0], v.entity.iloc[0], v.x_Note.iloc[0]) == ("V1", "DE01", "hi")


def test_txt_is_delimited(tmp_path):
    (tmp_path / "v.txt").write_text("id\tname\nV1\tAcme\n", encoding="utf-8")
    m = _mapping(tmp_path, "  vendors:\n    files: ['v.txt']\n    columns: {vendor_id: id, name: name}\n")
    assert list(load_dataset(tmp_path, m).table("vendors").name) == ["Acme"]


def test_profile_lists_unreadable_files(tmp_path):
    (tmp_path / "ok.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("a\tb\n1\t2\n", encoding="utf-8")
    (tmp_path / "legacy.xls").write_bytes(b"\xd0\xcf\x11\xe0junk")
    (tmp_path / "broken.xlsx").write_bytes(b"not a zip")
    (tmp_path / "inv.pdf").write_bytes(b"%PDF-1.4")
    out = profile_dir(tmp_path)
    assert "## notes.txt" in out
    tail = out[out.index("Files not profiled"):]
    for name in ("legacy.xls", "broken.xlsx", "inv.pdf"):
        assert name in tail
    assert "ok.csv" not in tail and "notes.txt" not in tail
    assert "unsupported file type" in tail


def test_unparsed_values_noted(tmp_path, caplog):
    (tmp_path / "i.csv").write_text("id,d,g\nA,31/10/2026,10\nB,n/a,x\nC,,5\nD,soon,7\n",
                                    encoding="utf-8")
    m = _mapping(tmp_path, "  invoices:\n    files: ['i.csv']\n"
                           "    columns: {invoice_id: id, invoice_date: d, gross_amount: g}\n")
    with caplog.at_level(logging.WARNING, logger="closeops.ingest"):
        ds = load_dataset(tmp_path, m)
    assert "invoices.invoice_date: 2/3 unparsed" in ds.notes
    assert "invoices.gross_amount: 1/4 unparsed" in ds.notes
    assert "invoices.invoice_date: 2/3 unparsed" in caplog.text


def test_date_order_mapping_level_and_per_spec(tmp_path):
    (tmp_path / "a.csv").write_text("c,d\nUSD,10/31/2026\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("c,d\nGBP,31/10/2026\n", encoding="utf-8")
    m = _mapping(tmp_path, """  fx_rates:
    - {files: ['a.csv'], columns: {currency: c, date: d}}
    - {files: ['b.csv'], columns: {currency: c, date: d}, date_order: dmy}
""", head="date_order: mdy\n")
    ds = load_dataset(tmp_path, m)
    assert list(ds.table("fx_rates").date) == [date(2026, 10, 31)] * 2
    assert ds.notes == []


def test_ids_repeating_across_entities_noted(tmp_path, caplog):
    (tmp_path / "i.csv").write_text("id,e\nI1,DE01\nI1,FR01\nI2,DE01\nI2,DE01\n", encoding="utf-8")
    m = _mapping(tmp_path, "  invoices:\n    files: ['i.csv']\n    columns: {invoice_id: id, entity: e}\n")
    with caplog.at_level(logging.WARNING, logger="closeops.ingest"):
        ds = load_dataset(tmp_path, m)
    note = [n for n in ds.notes if n.startswith("invoices.invoice_id")]
    assert len(note) == 1 and "1 id" in note[0] and "id_prefix_entity" in note[0]
    assert "repeat across entities" in caplog.text


def test_id_prefix_entity(tmp_path):
    (tmp_path / "inv.csv").write_text("id,e,po\nI1,DE01,P1\nI1,FR01,P1\nI3,FR01,\n", encoding="utf-8")
    (tmp_path / "po.csv").write_text("po,e\nP1,DE01\nP1,FR01\n", encoding="utf-8")
    (tmp_path / "gr_de.csv").write_text("gr,po,q\nG1,P1,3\n", encoding="utf-8")
    (tmp_path / "gr_fr.csv").write_text("gr,po,q,Co\nG2,P1,4,FR01\n", encoding="utf-8")
    m = _mapping(tmp_path, """  invoices:
    files: ['inv.csv']
    columns: {invoice_id: id, entity: e, po_id: po}
    id_prefix_entity: true
  purchase_orders:
    files: ['po.csv']
    columns: {po_id: po, entity: e}
    id_prefix_entity: true
  goods_receipts:
    - {files: ['gr_de.csv'], columns: {gr_id: gr, po_id: po, qty_received: q},
       defaults: {entity: DE01}, id_prefix_entity: true}
    - {files: ['gr_fr.csv'], columns: {gr_id: gr, po_id: po, qty_received: q, entity: Co},
       id_prefix_entity: true}
""")
    ds = load_dataset(tmp_path, m)
    inv = ds.table("invoices")
    assert list(inv.invoice_id) == ["DE01:I1", "FR01:I1", "FR01:I3"]
    assert list(inv.po_id[:2]) == ["DE01:P1", "FR01:P1"] and pd.isna(inv.po_id.iloc[2])
    assert list(ds.table("purchase_orders").po_id) == ["DE01:P1", "FR01:P1"]
    assert list(ds.table("goods_receipts").po_id) == ["DE01:P1", "FR01:P1"]
    assert ds.notes == []


def test_id_prefix_without_entity_noted(tmp_path):
    (tmp_path / "gr.csv").write_text("gr,po\nG1,P1\n", encoding="utf-8")
    m = _mapping(tmp_path, "  goods_receipts:\n    files: ['gr.csv']\n"
                           "    columns: {gr_id: gr, po_id: po}\n    id_prefix_entity: true\n")
    ds = load_dataset(tmp_path, m)
    assert list(ds.table("goods_receipts").po_id) == ["P1"]
    assert any("id_prefix_entity" in n and "goods_receipts" in n for n in ds.notes)
