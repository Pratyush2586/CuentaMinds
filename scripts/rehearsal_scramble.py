"""Rehearsal helper: turn a synthetic dataset into an "unseen" one.

Renames and reorders the columns of the invoice, vendor, bank-statement and GL files and
gives those files new names, so the shipped mappings.synth.yaml no longer fits and a
mapping has to be written from the profile alone. Everything else is copied unchanged.
answer_key.json is moved next to the output dir (DST/../answer_key.json) so it is not
part of the "unseen" raw data. Deterministic: the same input always gives the same output.

Usage: python scripts/rehearsal_scramble.py SRC_RAW_DIR DST_RAW_DIR
"""
from __future__ import annotations

import csv
import io
import random
import shutil
import sys
from pathlib import Path

ORDER_SEED = 17
# source file (or glob) -> (new name template, column renames)
SCRAMBLE = {
    "invoices.csv": ("ap_invoice_register.csv", {
        "Doc ID": "AP Document", "Company Code": "BUKRS", "Supplier No": "Creditor",
        "Invoice No.": "Ext. Reference", "Invoice Date": "Doc. Date",
        "Service Period End": "Perf. Period To", "PO Number": "Purch. Doc.",
        "Item": "Short Text", "Qty": "Quantity", "Unit Price": "Price/Unit",
        "Currency": "Crcy", "Net": "Net Value", "VAT %": "Tax Rate", "VAT": "Tax Value",
        "Gross": "Gross Value", "Supplier Country": "Creditor Ctry",
        "Buyer Country": "Co. Ctry", "Status": "Pay Status"}),
    "vendors.csv": ("creditor_master.csv", {
        "Vendor No": "Creditor", "Company": "BUKRS", "Vendor Name": "Name 1",
        "Country": "Ctry", "VAT Reg No": "Tax Number", "IBAN": "Bank Acct (IBAN)",
        "Credit Limit": "Limit", "Payment Terms": "Terms"}),
    "bank_statement_*.csv": ("stmt_{stem}_oct.csv", {
        "Company": "Entity", "Account": "Acct", "Booking Date": "Book. Dt",
        "Value Date": "Val. Dt", "Amount": "Amt (signed)", "Currency": "Ccy",
        "Counterparty": "Payee/Payer", "Counterparty IBAN": "Payee Account",
        "Remittance Information": "Narrative", "Transaction ID": "Bank Ref"}),
    "gl.csv": ("journal_export.csv", {
        "JE Number": "Doc No", "Company": "BUKRS", "Posting Date": "Pstng Date",
        "GL Account": "G/L Acct", "Debit": "Dr", "Credit": "Cr", "Curr": "Crcy",
        "Text": "Line Text", "Trading Partner": "Partner Co."}),
}


def _scramble_csv(text: str, renames: dict[str, str], rng: random.Random) -> str:
    delim = ";" if text.split("\n", 1)[0].count(";") > text.split("\n", 1)[0].count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header = rows[0]
    order = list(range(len(header)))
    rng.shuffle(order)
    out = io.StringIO()
    w = csv.writer(out, delimiter=delim, lineterminator="\n")
    w.writerow([renames.get(header[i], header[i]) for i in order])
    for r in rows[1:]:
        w.writerow([r[i] if i < len(r) else "" for i in order])
    return out.getvalue()


def scramble(src: Path, dst: Path) -> None:
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    rng = random.Random(ORDER_SEED)
    done: set[Path] = set()
    for pattern, (template, renames) in SCRAMBLE.items():
        for path in sorted(src.glob(pattern)):
            stem = path.stem.rsplit("_", 1)[-1]
            text = path.read_text(encoding="utf-8")
            (dst / template.format(stem=stem)).write_text(
                _scramble_csv(text, renames, rng), encoding="utf-8", newline="")
            done.add(path)
    for path in sorted(src.iterdir()):
        if path in done or not path.is_file():
            continue
        target = dst.parent / path.name if path.name == "answer_key.json" else dst / path.name
        shutil.copyfile(path, target)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    scramble(Path(sys.argv[1]), Path(sys.argv[2]))
    print(sys.argv[2])
