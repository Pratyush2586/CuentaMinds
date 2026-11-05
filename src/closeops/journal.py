"""Proposed adjusting journal entries and the payment proposal list (propose only, never post)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from closeops.checks._util import FxRates, _plain
from closeops.model import Dataset, Finding

JE_COLUMNS = ["je_id", "entity", "date", "account", "debit", "credit", "currency", "reason",
              "finding_id", "tier"]
PAY_COLUMNS = ["invoice_id", "vendor_id", "entity", "amount", "currency", "iban", "due_date",
               "tier", "block_reason"]
OPEN_STATUSES = {"open", "unpaid", "pending"}
DEFAULT_TERMS_DAYS = 30


def _num(v) -> float | None:
    v = _plain(v)
    return None if v is None else float(v)


def _cur(v) -> str:
    v = _plain(v)
    return str(v).strip().upper() if v is not None and str(v).strip() else "EUR"


def _text(v) -> str | None:
    v = _plain(v)
    return None if v is None or not str(v).strip() else str(v).strip()


def _row(df: pd.DataFrame, col: str, key: str) -> dict | None:
    if df.empty or col not in df.columns:
        return None
    hit = df[df[col].astype(str) == str(key)]
    return None if hit.empty else hit.iloc[0].to_dict()


def _lines(*lines: tuple[str, float, float]) -> list[tuple[str, float, float]]:
    out = [(a, round(d, 2), round(c, 2)) for a, d, c in lines]
    return [ln for ln in out if ln[1] or ln[2]]


def _invoice_lines(f: Finding, ds: Dataset, acc: dict):
    inv = _row(ds.table("invoices"), "invoice_id", f.record_ids[0]) if f.record_ids else None
    if inv is None:
        return None
    net, gross, tax = _num(inv["net_amount"]), _num(inv["gross_amount"]), _num(inv["tax_amount"])
    if gross is None:
        return None
    if net is None:
        net = gross - (tax or 0.0)
    if tax is None:
        tax = gross - net
    ln = _lines((acc["ap"], gross, 0), (acc["expense_default"], 0, net), (acc["vat_input"], 0, tax))
    return ln, _cur(inv["currency"]), _text(inv["entity"]) or f.entity


def _je_lines(f: Finding, ds: Dataset, acc: dict):
    """-> (lines, currency, entity) or None when the finding yields no entry."""
    ev = f.evidence
    if f.check.startswith("dup_") and f.record_type == "invoice":
        return _invoice_lines(f, ds, acc)
    if f.check == "tax_amount":
        exp, booked = _num(ev.get("expected_tax")), _num(ev.get("booked_tax"))
        if exp is None or booked is None or round(exp - booked, 2) == 0:
            return None
        d = exp - booked
        ap, vat = acc["ap"], acc["vat_input"]
        ln = _lines((vat, d, 0), (ap, 0, d)) if d > 0 else _lines((ap, -d, 0), (vat, 0, -d))
        return ln, _cur(ev.get("currency")), f.entity
    if f.check == "cutoff":
        net, kind = _num(ev.get("net_amount")), ev.get("kind")
        if net is None or round(net, 2) == 0 or kind not in ("accrual", "deferral"):
            return None
        inv = _row(ds.table("invoices"), "invoice_id", f.record_ids[0]) if f.record_ids else None
        exp = acc["expense_default"]
        ln = (_lines((exp, net, 0), (acc["accrued"], 0, net)) if kind == "accrual"
              else _lines((acc["deferred"], net, 0), (exp, 0, net)))
        return ln, _cur(inv["currency"]) if inv else "EUR", f.entity
    if f.check == "bank_unmatched":
        t = _row(ds.table("bank_transactions"), "txn_id", f.record_ids[0]) if f.record_ids else None
        amt = _num(t["amount"]) if t else None
        if amt is None or round(amt, 2) == 0:
            return None
        a = abs(amt)
        ln = (_lines((acc["suspense"], a, 0), (acc["bank"], 0, a)) if amt < 0
              else _lines((acc["bank"], a, 0), (acc["suspense"], 0, a)))
        return ln, _cur(t["currency"]), _text(t["entity"]) or f.entity
    return None


def propose_journal_entries(ds: Dataset, findings: list[Finding], policy: dict) -> pd.DataFrame:
    acc = policy["accounts"]
    rows: list[dict] = []
    seq = 0
    for f in sorted(findings, key=lambda x: x.finding_id):
        res = _je_lines(f, ds, acc)
        if res is None or not res[0]:
            continue
        lines, cur, entity = res
        if round(sum(d for _, d, _ in lines) - sum(c for _, _, c in lines), 2) != 0:
            continue  # never emit an unbalanced entry
        seq += 1
        for account, debit, credit in lines:
            rows.append({"je_id": f"ADJ-{seq:04d}", "entity": entity, "date": ds.period_end,
                         "account": account, "debit": debit, "credit": credit, "currency": cur,
                         "reason": f.check, "finding_id": f.finding_id,
                         "tier": f.tier.value if f.tier is not None else ""})
    return pd.DataFrame(rows, columns=JE_COLUMNS)


def _open_mask(inv: pd.DataFrame, bank_matches: pd.DataFrame | None) -> tuple[pd.Series, bool]:
    """-> (open invoices, status_known). Without any status, unmatched invoices count as
    open but the status is unknown, so their proposals are held."""
    status = inv["status"].map(lambda v: (_text(v) or "").lower() or None)
    if status.notna().any():
        return status.isin(OPEN_STATUSES), True
    matched: set[str] = set()
    if bank_matches is not None and not bank_matches.empty:
        for ids in bank_matches["matched_ids"]:
            matched |= {str(i) for i in (ids if isinstance(ids, (list, tuple, set)) else [ids])}
    return ~inv["invoice_id"].astype(str).isin(matched), False


def _terms(ds: Dataset) -> dict[str, int]:
    out: dict[str, int] = {}
    if not ds.has("contracts"):
        return out
    for c in ds.table("contracts").to_dict("records"):
        vf, vt, days = _plain(c["valid_from"]), _plain(c["valid_to"]), _num(c["payment_terms_days"])
        if days is None:
            continue
        if (isinstance(vf, date) and vf > ds.period_end) or (isinstance(vt, date) and vt < ds.period_end):
            continue
        out.setdefault(str(c["vendor_id"]), int(days))
    return out


def _iban_changed_vendors(ds: Dataset, policy: dict) -> set[str]:
    """Vendors whose bank details changed within the window before period end, or at an
    unknown time, or after period end (hard rule: never auto-pay these)."""
    window = int(policy["fraud"]["iban_change_window_days"])
    out: set[str] = set()
    if not ds.has("vendor_bank_history"):
        return out
    for h in ds.table("vendor_bank_history").to_dict("records"):
        d = _plain(h["changed_at"])
        if not isinstance(d, date) or (ds.period_end - d).days <= window:
            out.add(str(h["vendor_id"]))
    return out


Key = tuple[str, str]  # (record_type, id): ids from different tables never collide


def _finding_hits(findings: list[Finding]) -> tuple[dict[Key, str], dict[Key, str]]:
    """Index findings: fraud/duplicate by record or vendor (block), match/tax by record."""
    block: dict[Key, str] = {}
    inv_issue: dict[Key, str] = {}
    for f in sorted(findings, key=lambda x: x.finding_id):
        if f.category in ("fraud", "duplicate"):
            # a duplicate holds only the later copy (record_ids[0]), never the original
            ids = f.record_ids[:1] if f.category == "duplicate" else f.record_ids
            keys = [(f.record_type, str(k)) for k in ids]
            if f.category == "fraud" and f.severity == "high" and f.evidence.get("vendor_id"):
                # compromised bank details: vendor-wide hold
                keys.append(("vendor", str(f.evidence["vendor_id"])))
            for k in keys:
                block.setdefault(k, f.check)
        elif f.category in ("match", "tax"):
            for k in f.record_ids:
                inv_issue.setdefault((f.record_type, str(k)), f.check)
    return block, inv_issue


def propose_payments(ds: Dataset, findings: list[Finding], policy: dict,
                     bank_matches: pd.DataFrame | None = None) -> pd.DataFrame:
    if not ds.has("invoices"):
        return pd.DataFrame(columns=PAY_COLUMNS)
    inv = ds.table("invoices")
    open_mask, status_known = _open_mask(inv, bank_matches)
    inv = inv[open_mask]
    vendors = {str(v["vendor_id"]): _text(v["iban"]) for v in ds.table("vendors").to_dict("records")}
    terms, changed = _terms(ds), _iban_changed_vendors(ds, policy)
    block, inv_issue = _finding_hits(findings)
    fx, mat = FxRates(ds), float(policy["materiality_eur"])
    rows = []
    for r in inv.to_dict("records"):
        net, gross = _num(r["net_amount"]), _num(r["gross_amount"])
        if gross is None or gross < 0 or (net is not None and net < 0):  # credit notes
            continue
        iid, vid, cur = str(r["invoice_id"]), str(r["vendor_id"]), _cur(r["currency"])
        idate = _plain(r["invoice_date"])
        due = idate + timedelta(days=terms.get(vid, DEFAULT_TERMS_DAYS)) if isinstance(idate, date) else None
        iban = vendors.get(vid)
        rate = fx.rate(cur, ds.period_end)
        if vid in changed:
            reason = "iban_changed"
        elif ("invoice", iid) in block or ("vendor", vid) in block:
            reason = block.get(("invoice", iid)) or block[("vendor", vid)]
        elif ("invoice", iid) in inv_issue:
            reason = inv_issue[("invoice", iid)]
        elif not status_known:
            reason = "status_unknown"
        elif not iban:
            reason = "no_iban"
        elif rate is not None and round(abs(gross) * rate, 2) >= mat:
            reason = "materiality"
        else:
            reason = ""
        rows.append({"invoice_id": iid, "vendor_id": vid, "entity": _text(r["entity"]) or "",
                     "amount": gross, "currency": cur, "iban": iban or "", "due_date": due,
                     "tier": "HOLD" if reason else "AUTO", "block_reason": reason})
    return pd.DataFrame(rows, columns=PAY_COLUMNS)
