"""Bank reconciliation: match bank lines to invoices (and bank-account GL rows).

Passes run in order reference -> exact -> window -> group -> fx -> gl. Each pass walks
the still-unmatched bank lines of one entity by (txn_date, txn_id) before the next pass
starts. Each invoice and each GL row is matched at most once. Outgoing lines
(amount < 0) pay invoices (net_amount > 0); incoming lines (amount > 0) may match credit
notes (gross_amount < 0). Amounts compare as absolute values; differences are rounded to
6 decimals before the tolerance test. Lines left unmatched become `bank_unmatched`
findings.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from closeops.checks._util import FxRates as _Fx, _iso
from closeops.checks.fraud import _iban, _records, _s, _Vendors
from closeops.model import Dataset, Finding

COLUMNS = ["txn_id", "matched_ids", "method", "confidence"]
CONFIDENCE = {"reference": 0.99, "exact": 0.95, "window": 0.8, "group": 0.8, "fx": 0.75}
GL_CONFIDENCE = {"exact": 0.9, "window": 0.8}
# Performance bound for the group search: only the invoices nearest in date are tried.
_GROUP_CANDIDATE_CAP = 25
_UNMATCHED_ACTION = ("Investigate the unmatched bank line: find the invoice, receipt or "
                     "journal entry it settles, or book it.")


@dataclass
class _Line:
    id: str
    entity: str
    date: date | None
    amount: float | None
    currency: str
    iban: str | None
    name: str | None
    reference: str | None
    ref_key: str


@dataclass
class _Inv:
    id: str
    entity: str
    vendor: str | None
    date: date
    gross: float
    currency: str
    no_pattern: re.Pattern | None
    outgoing: bool  # True: paid by an outgoing line; False: credit note, incoming line


def _f(v) -> float | None:
    return None if v is None else float(v)


def _cur(v) -> str:
    s = _s(v)
    return s.upper() if s else "EUR"


def _alnum(v) -> str:
    s = _s(v)
    return re.sub(r"[^A-Za-z0-9]", "", s).upper() if s else ""


def _no_pattern(invoice_no) -> re.Pattern | None:
    """Regex finding the invoice number in an alphanumeric-only reference.

    Leading zeros of the trailing digit run are optional and the run may not continue
    into further digits, so INV-0123 matches 'INV00123X' but not 'INV1234'.
    """
    s = _alnum(invoice_no)
    if not s:
        return None
    m = re.search(r"\d+$", s)
    if not m:
        return re.compile(re.escape(s))
    prefix, digits = s[: m.start()], m.group().lstrip("0") or "0"
    lead = "" if prefix else r"(?<!\d)"
    return re.compile(lead + re.escape(prefix) + "0*" + digits + r"(?!\d)")


def _equal(a: float, b: float, tol: float) -> bool:
    d = a - b
    if d > tol + 1e-6 or d < -tol - 1e-6:  # cheap reject before the rounded comparison
        return False
    return abs(round(d, 6)) <= tol


def _load_lines(ds: Dataset) -> list[_Line]:
    out = []
    for r in _records(ds, "bank_transactions"):
        d = r["txn_date"]
        out.append(_Line(id=str(r["txn_id"]), entity=str(r["entity"]),
                         date=d if isinstance(d, date) else None, amount=_f(r["amount"]),
                         currency=_cur(r["currency"]), iban=_iban(r["counterparty_iban"]),
                         name=_s(r["counterparty_name"]), reference=_s(r["reference"]),
                         ref_key=_alnum(r["reference"])))
    return sorted(out, key=lambda x: (x.date is None, x.date or date.min, x.id))


def _load_invoices(ds: Dataset) -> list[_Inv]:
    out = []
    for r in _records(ds, "invoices"):
        net, gross, d = _f(r["net_amount"]), _f(r["gross_amount"]), r["invoice_date"]
        if gross is None or gross == 0 or not isinstance(d, date):
            continue
        if net is not None and net > 0:
            outgoing = True
        elif gross < 0:
            outgoing = False
        else:
            continue
        out.append(_Inv(id=str(r["invoice_id"]), entity=str(r["entity"]),
                        vendor=_s(r["vendor_id"]), date=d, gross=gross,
                        currency=_cur(r["currency"]), no_pattern=_no_pattern(r["invoice_no"]),
                        outgoing=outgoing))
    return out


class _Matcher:
    def __init__(self, ds: Dataset, cfg: dict):
        self.window = int(cfg["date_window_days"])
        self.lag = int(cfg["max_payment_lag_days"])
        self.max_group = int(cfg["max_group"])
        self.tol = float(cfg["amount_tolerance"])
        self.fx_tol = float(cfg["fx_tolerance_pct"])
        self.vendors = _Vendors(ds)
        self.fx = _Fx(ds)
        self.lines = _load_lines(ds)
        self.invoices: dict[str, list[_Inv]] = defaultdict(list)  # per entity, by date
        for i in sorted(_load_invoices(ds), key=lambda i: (i.date, i.id)):
            self.invoices[i.entity].append(i)
        self.inv_dates = {e: [i.date for i in v] for e, v in self.invoices.items()}
        self.used: set[str] = set()
        self._links: dict[str, set[str]] = {}  # txn_id -> vendor ids the counterparty links to
        self.matches: dict[str, dict] = {}

    # -- helpers -------------------------------------------------------------
    def _linkable(self, ln: _Line) -> bool:
        """Counterparty can be checked: vendor master present and line has IBAN or name."""
        return self.vendors.present and (ln.iban is not None or ln.name is not None)

    def _linked(self, ln: _Line, inv: _Inv) -> bool:
        if ln.id not in self._links:
            self._links[ln.id] = self.vendors.linked_vendors(ln.iban, ln.name)
        return inv.vendor in self._links[ln.id]

    def _open(self, ln: _Line) -> list[_Inv]:
        """Unmatched invoices of the line's entity, right direction, inside the timing window."""
        if ln.date is None or not ln.amount:
            return []
        outgoing = ln.amount < 0
        invs, dates = self.invoices.get(ln.entity, []), self.inv_dates.get(ln.entity, [])
        lo = bisect_left(dates, ln.date - timedelta(days=self.lag))
        hi = bisect_right(dates, ln.date + timedelta(days=self.window))
        return [i for i in invs[lo:hi] if i.id not in self.used and i.outgoing == outgoing]

    def _pick(self, ln: _Line, cands: list[_Inv]) -> _Inv | None:
        if not cands:
            return None
        return min(cands, key=lambda i: (abs((ln.date - i.date).days), i.id))

    def _record(self, ln: _Line, invs: list[_Inv], method: str) -> None:
        self.used.update(i.id for i in invs)
        self.matches[ln.id] = {"txn_id": ln.id, "matched_ids": sorted(i.id for i in invs),
                               "method": method, "confidence": CONFIDENCE[method]}

    # -- passes --------------------------------------------------------------
    def _reference(self, ln: _Line) -> _Inv | None:
        if not ln.ref_key:
            return None
        return self._pick(ln, [i for i in self._open(ln)
                               if i.currency == ln.currency and i.no_pattern is not None
                               and i.no_pattern.search(ln.ref_key)
                               and _equal(abs(ln.amount), abs(i.gross), self.tol)])

    def _same_amount(self, ln: _Line, linked: bool) -> _Inv | None:
        if self._linkable(ln) != linked:
            return None
        return self._pick(ln, [i for i in self._open(ln)
                               if i.currency == ln.currency
                               and _equal(abs(ln.amount), abs(i.gross), self.tol)
                               and (not linked or self._linked(ln, i))])

    def _group(self, ln: _Line) -> list[_Inv] | None:
        linkable = self._linkable(ln)
        by_vendor: dict[str, list[_Inv]] = defaultdict(list)
        for i in self._open(ln):
            if i.vendor is None or i.currency != ln.currency:
                continue
            if linkable and not self._linked(ln, i):
                continue
            by_vendor[i.vendor].append(i)
        best, best_key = None, None
        for vid in sorted(by_vendor):
            found = _best_group(ln, by_vendor[vid], abs(ln.amount), self.tol, self.max_group)
            if found is None:
                continue
            key = _group_key(ln, found)
            if best_key is None or key < best_key:
                best, best_key = found, key
        return best

    def _fx_match(self, ln: _Line) -> _Inv | None:
        """Both sides convert to EUR at the rate for the txn date (nearest on or before)."""
        bank_rate = self.fx.rate(ln.currency, ln.date)
        if bank_rate is None:
            return None
        bank_eur = abs(ln.amount) * bank_rate
        linkable = self._linkable(ln)
        cands = []
        for i in self._open(ln):
            if i.currency == ln.currency or (linkable and not self._linked(ln, i)):
                continue
            rate = self.fx.rate(i.currency, ln.date)
            if rate is None:
                continue
            inv_eur = abs(i.gross) * rate
            if inv_eur and abs(round((bank_eur - inv_eur) / inv_eur * 100, 6)) <= self.fx_tol:
                cands.append(i)
        return self._pick(ln, cands)

    def run_invoice_passes(self) -> None:
        by_entity: dict[str, list[_Line]] = defaultdict(list)
        for ln in self.lines:
            by_entity[ln.entity].append(ln)
        passes = [
            ("reference", lambda ln: self._reference(ln)),
            ("exact", lambda ln: self._same_amount(ln, linked=True)),
            ("window", lambda ln: self._same_amount(ln, linked=False)),
            ("group", lambda ln: self._group(ln)),
            ("fx", lambda ln: self._fx_match(ln)),
        ]
        for entity in sorted(by_entity):
            for method, find in passes:
                for ln in by_entity[entity]:
                    if ln.id in self.matches or ln.date is None or not ln.amount:
                        continue  # null date/amount lines cannot match an invoice
                    hit = find(ln)
                    if hit:
                        self._record(ln, hit if isinstance(hit, list) else [hit], method)

    def run_gl_pass(self, ds: Dataset) -> None:
        if not ds.has("accounts") or not ds.has("gl_entries"):
            return
        bank_accounts = {_s(r["account"]) for r in _records(ds, "accounts")
                         if (_s(r["type"]) or "").lower() == "bank"}
        rows = []
        for idx, r in enumerate(_records(ds, "gl_entries")):
            d = r["posting_date"]
            if _s(r["account"]) not in bank_accounts or not isinstance(d, date):
                continue
            rows.append({"idx": idx, "je_id": str(r["je_id"]), "entity": str(r["entity"]),
                         "date": d, "amount": (_f(r["debit"]) or 0.0) - (_f(r["credit"]) or 0.0)})
        used: set[int] = set()
        for ln in self.lines:
            if ln.id in self.matches or ln.date is None or ln.amount is None:
                continue
            cands = [g for g in rows
                     if g["idx"] not in used and g["entity"] == ln.entity
                     and abs((g["date"] - ln.date).days) <= self.window
                     and _equal(g["amount"], ln.amount, self.tol)]
            if not cands:
                continue
            g = min(cands, key=lambda g: (abs((g["date"] - ln.date).days), g["je_id"], g["idx"]))
            used.add(g["idx"])
            method = "exact" if g["date"] == ln.date else "window"
            self.matches[ln.id] = {"txn_id": ln.id, "matched_ids": [g["je_id"]],
                                   "method": method, "confidence": GL_CONFIDENCE[method]}

    # -- unmatched -----------------------------------------------------------
    def _nearest_candidates(self, ln: _Line) -> list[str]:
        linkable = self._linkable(ln)
        cands = [i for i in self.invoices[ln.entity]
                 if (linkable and self._linked(ln, i))
                 or (ln.amount is not None and _equal(abs(ln.amount), abs(i.gross), self.tol))]
        far = 10 ** 9
        cands.sort(key=lambda i: (abs((ln.date - i.date).days) if ln.date else far, i.id))
        return [i.id for i in cands[:3]]

    def unmatched_findings(self) -> list[Finding]:
        out = []
        for ln in self.lines:
            if ln.id in self.matches:
                continue
            rate = self.fx.rate(ln.currency, ln.date)
            amount_eur = 0.0 if ln.amount is None else round(abs(ln.amount) * (rate or 1.0), 2)
            evidence = {"amount": ln.amount, "currency": ln.currency, "txn_date": _iso(ln.date),
                        "counterparty_name": ln.name, "counterparty_iban": ln.iban,
                        "reference": ln.reference,
                        "nearest_candidates": self._nearest_candidates(ln)}
            out.append(Finding(check="bank_unmatched", category="bankrec", severity="medium",
                               entity=ln.entity, record_type="bank_transaction",
                               record_ids=[ln.id], evidence=evidence, confidence=0.7,
                               suggested_action=_UNMATCHED_ACTION, amount_eur=amount_eur))
        return out


def _group_key(ln: _Line, group: list[_Inv]) -> tuple:
    """Smaller group first, then closest in date, then lowest invoice ids."""
    return (len(group), sum(abs((ln.date - i.date).days) for i in group),
            sorted(i.id for i in group))


def _best_group(ln: _Line, cands: list[_Inv], target: float, tol: float,
                max_group: int) -> list[_Inv] | None:
    """Smallest set of 2..max_group invoices whose absolute gross sums to target.

    Bounded: only the invoices nearest in date are tried, and the depth-first search
    prunes on sorted amounts.
    """
    cands = sorted(cands, key=lambda i: (abs((ln.date - i.date).days), i.id))
    cands = sorted(cands[:_GROUP_CANDIDATE_CAP], key=lambda i: (abs(i.gross), i.id))
    amounts = [abs(i.gross) for i in cands]
    n = len(amounts)
    for k in range(2, max_group + 1):
        if k > n:
            break
        best: list[_Inv] | None = None

        def dfs(start: int, left: int, total: float, chosen: list[int]) -> None:
            nonlocal best
            if left == 0:
                if _equal(total, target, tol):
                    group = [cands[j] for j in chosen]
                    if best is None or _group_key(ln, group) < _group_key(ln, best):
                        best = group
                return
            if round(total + sum(amounts[n - left:]) - target, 6) < -tol:
                return
            for j in range(start, n - left + 1):
                if round(total + sum(amounts[j:j + left]) - target, 6) > tol:
                    break
                dfs(j + 1, left - 1, total + amounts[j], chosen + [j])

        dfs(0, k, 0.0, [])
        if best is not None:
            return best
    return None


def reconcile_bank(ds: Dataset, policy: dict) -> tuple[pd.DataFrame, list[Finding]]:
    m = _Matcher(ds, policy["bankrec"])
    m.run_invoice_passes()
    m.run_gl_pass(ds)
    rows = [m.matches[k] for k in sorted(m.matches)]
    matches = pd.DataFrame(rows, columns=COLUMNS)
    findings = sorted(m.unmatched_findings(), key=lambda f: f.finding_id)
    return matches, findings
