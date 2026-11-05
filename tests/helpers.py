"""Row builders shared by tests (canonical types: dates as date, amounts as float)."""
from __future__ import annotations

from datetime import date


def inv(invoice_id, vendor_id, invoice_no, invoice_date: str, gross: float, **extra) -> dict:
    row = {
        "invoice_id": invoice_id,
        "entity": "E1",
        "vendor_id": vendor_id,
        "invoice_no": invoice_no,
        "invoice_date": date.fromisoformat(invoice_date),
        "net_amount": gross,
        "tax_amount": 0.0,
        "gross_amount": gross,
        "currency": "EUR",
    }
    row.update(extra)
    return row


def txn(txn_id, txn_date: str, amount: float, iban: str, **extra) -> dict:
    row = {
        "txn_id": txn_id,
        "entity": "E1",
        "txn_date": date.fromisoformat(txn_date),
        "amount": amount,
        "currency": "EUR",
        "counterparty_iban": iban,
    }
    row.update(extra)
    return row
