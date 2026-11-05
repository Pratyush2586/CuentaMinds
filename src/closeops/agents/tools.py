"""Evidence tools for the agent team: read-only lookups over the canonical dataset.

Plain functions take `ds` first and return JSON-safe dicts/lists (at most MAX_ROWS rows).
`TOOL_SPECS` + `dispatch` expose them to the Messages API tool loop.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from difflib import SequenceMatcher

import pandas as pd

from closeops.checks._util import _plain
from closeops.model import Dataset

MAX_ROWS = 25
_ID_COLUMN = {"invoice": ("invoices", "invoice_id"),
              "bank_transaction": ("bank_transactions", "txn_id"),
              "gl_entry": ("gl_entries", "je_id"),
              "vendor": ("vendors", "vendor_id")}


def _clean(v):
    v = _plain(v)
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _rows(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.head(MAX_ROWS).to_dict("records")]


def get_records(ds: Dataset, record_type: str, ids: list[str]) -> list[dict]:
    if record_type not in _ID_COLUMN:
        return [{"error": f"unknown record_type {record_type!r}; use one of {sorted(_ID_COLUMN)}"}]
    table, col = _ID_COLUMN[record_type]
    if not ds.has(table):
        return []
    df = ds.table(table)
    wanted = {str(i) for i in ids}
    return _rows(df[df[col].astype(str).isin(wanted)])


def vendor_profile(ds: Dataset, vendor_id: str) -> dict:
    vid = str(vendor_id)
    out: dict = {"vendor_id": vid, "vendor": None, "bank_history": [], "invoice_stats": {}}
    if ds.has("vendors"):
        v = ds.table("vendors")
        rows = _rows(v[v["vendor_id"].astype(str) == vid])
        out["vendor"] = rows[0] if rows else None
    if ds.has("vendor_bank_history"):
        h = ds.table("vendor_bank_history")
        h = h[h["vendor_id"].astype(str) == vid].sort_values("changed_at")
        out["bank_history"] = _rows(h)
    if ds.has("invoices"):
        i = ds.table("invoices")
        i = i[i["vendor_id"].astype(str) == vid]
        gross = pd.to_numeric(i["gross_amount"], errors="coerce")
        out["invoice_stats"] = {
            "count": int(len(i)),
            "total_gross": round(float(gross.sum()), 2) if len(i) else 0.0,
            "avg_gross": round(float(gross.mean()), 2) if gross.notna().any() else None,
            "max_gross": round(float(gross.max()), 2) if gross.notna().any() else None,
            "currencies": sorted({str(c) for c in i["currency"].dropna()}),
        }
    return out


def similar_invoices(ds: Dataset, invoice_id: str) -> list[dict]:
    if not ds.has("invoices"):
        return []
    df = ds.table("invoices")
    base = df[df["invoice_id"].astype(str) == str(invoice_id)]
    if base.empty:
        return []
    b = base.iloc[0]
    no = str(_plain(b["invoice_no"]) or "")
    gross = pd.to_numeric(df["gross_amount"], errors="coerce")
    same_vendor = df["vendor_id"].astype(str) == str(b["vendor_id"])
    same_amount = (gross - float(_plain(b["gross_amount"]) or 0)).abs() < 0.01
    ratio = df["invoice_no"].astype(str).map(lambda s: SequenceMatcher(None, no, s).ratio())
    mask = ((same_vendor & ((ratio >= 0.8) | same_amount)) | (same_amount & (ratio >= 0.8))) \
        & (df["invoice_id"].astype(str) != str(invoice_id))
    return _rows(df[mask])


def tax_rule(ds: Dataset, country: str) -> dict:
    c = str(country).strip().upper()
    rows: list[dict] = []
    if ds.has("tax_rates"):
        t = ds.table("tax_rates")
        rows = _rows(t[t["country"].astype(str).str.upper() == c])
    return {"country": c, "rates": rows}


def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties,
                             "required": required, "additionalProperties": False}}


TOOL_SPECS: list[dict] = [
    _spec("get_records", "Fetch canonical rows (with source_file/source_row) by id.",
          {"record_type": {"type": "string", "enum": sorted(_ID_COLUMN)},
           "ids": {"type": "array", "items": {"type": "string"}}},
          ["record_type", "ids"]),
    _spec("vendor_profile", "Vendor master row, IBAN change history and invoice statistics.",
          {"vendor_id": {"type": "string"}}, ["vendor_id"]),
    _spec("similar_invoices", "Other invoices with a similar number or same amount for the same vendor.",
          {"invoice_id": {"type": "string"}}, ["invoice_id"]),
    _spec("tax_rule", "Configured tax rates for a country (ISO code).",
          {"country": {"type": "string"}}, ["country"]),
]

_FUNCS = {"get_records": get_records, "vendor_profile": vendor_profile,
          "similar_invoices": similar_invoices, "tax_rule": tax_rule}


def dispatch(ds: Dataset, name: str, args: dict) -> dict:
    """Run one tool; always returns a JSON-safe dict (lists are wrapped under 'result')."""
    fn = _FUNCS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        out = fn(ds, **args)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    out = out if isinstance(out, dict) else {"result": out}
    return json.loads(json.dumps(out, default=str))
