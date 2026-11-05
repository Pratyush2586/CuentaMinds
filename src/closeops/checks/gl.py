"""GL / close checks: journal and trial-balance balance, intercompany, cutoff.

Monetary comparisons round to 2 decimals after summing and use a 0.01 tolerance.
Intercompany lines convert to EUR at the fx rate on/before the period end.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd

from closeops.checks._util import FxRates, _iso, _plain
from closeops.model import Dataset, Finding

TOL = 0.01
TB_COLUMNS = ["entity", "account", "debit", "credit", "balance"]


def _num(v) -> float:
    v = _plain(v)
    return 0.0 if v is None else float(v)


def _text(v) -> str | None:
    v = _plain(v)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _cur(v) -> str:
    s = _text(v)
    return s.upper() if s else "EUR"


def _records(ds: Dataset, name: str) -> list[dict]:
    if not ds.has(name):
        return []
    return [{k: _plain(v) for k, v in r.items()} for r in ds.table(name).to_dict("records")]


def _over(x: float) -> bool:
    return abs(round(x, 2)) > TOL + 1e-9


def _je_findings(ds: Dataset, fx: FxRates) -> list[Finding]:
    by_je: dict[str, dict] = defaultdict(lambda: {"debit": 0.0, "credit": 0.0, "entities": set(),
                                                   "currency": None})
    for r in _records(ds, "gl_entries"):
        je = _text(r["je_id"])
        if je is None:
            continue
        e = by_je[je]
        e["debit"] += _num(r["debit"])
        e["credit"] += _num(r["credit"])
        if _text(r["entity"]):
            e["entities"].add(_text(r["entity"]))
        if e["currency"] is None:
            e["currency"] = _cur(r["currency"])
    out = []
    for je, e in by_je.items():
        diff = round(e["debit"] - e["credit"], 2)
        if not _over(diff):
            continue
        rate = fx.rate(e["currency"], ds.period_end) or 1.0
        out.append(Finding(
            check="je_unbalanced", category="gl", severity="high",
            entity=sorted(e["entities"])[0] if e["entities"] else "",
            record_type="gl_entry", record_ids=[je],
            evidence={"debit": round(e["debit"], 2), "credit": round(e["credit"], 2),
                      "difference": diff},
            confidence=0.99, amount_eur=round(abs(diff) * rate, 2),
            suggested_action="Journal entry does not balance; correct or repost the entry."))
    return out


def _tb_findings(ds: Dataset, fx: FxRates) -> list[Finding]:
    by_entity: dict[str, dict] = defaultdict(lambda: {"debit": 0.0, "credit": 0.0, "currency": None})
    for r in _records(ds, "gl_entries"):
        ent = _text(r["entity"])
        if ent is None:
            continue
        e = by_entity[ent]
        e["debit"] += _num(r["debit"])
        e["credit"] += _num(r["credit"])
        if e["currency"] is None:
            e["currency"] = _cur(r["currency"])
    out = []
    for ent, e in by_entity.items():
        diff = round(e["debit"] - e["credit"], 2)
        if not _over(diff):
            continue
        rate = fx.rate(e["currency"], ds.period_end) or 1.0
        out.append(Finding(
            check="tb_unbalanced", category="gl", severity="high", entity=ent,
            record_type="entity", record_ids=[ent],
            evidence={"debit": round(e["debit"], 2), "credit": round(e["credit"], 2),
                      "difference": diff},
            confidence=0.99, amount_eur=round(abs(diff) * rate, 2),
            suggested_action="Trial balance does not balance; find the unbalanced entries."))
    return out


def _intercompany_findings(ds: Dataset, fx: FxRates) -> list[Finding]:
    restrict: set[str] | None = None
    if ds.has("accounts"):
        ic = {_text(r["account"]) for r in _records(ds, "accounts")
              if "intercompany" in (_text(r["type"]) or "").lower()}
        ic.discard(None)
        if ic:
            restrict = ic
    net: dict[tuple[str, str], float] = defaultdict(float)  # (entity, counterparty) -> EUR
    for r in _records(ds, "gl_entries"):
        ent, cp = _text(r["entity"]), _text(r["counterparty_entity"])
        if ent is None or cp is None or ent == cp:
            continue
        if restrict is not None and _text(r["account"]) not in restrict:
            continue
        rate = fx.rate(_cur(r["currency"]), ds.period_end)
        net[(ent, cp)] += (_num(r["debit"]) - _num(r["credit"])) * (1.0 if rate is None else rate)
    pairs = sorted({tuple(sorted(k)) for k in net})
    out = []
    for a, b in pairs:
        net_a, net_b = round(net.get((a, b), 0.0), 2), round(net.get((b, a), 0.0), 2)
        diff = round(net_a + net_b, 2)
        if not _over(diff):
            continue
        out.append(Finding(
            check="intercompany_mismatch", category="gl", severity="medium", entity=a,
            record_type="entity_pair", record_ids=[a, b],
            evidence={"net_a_eur": net_a, "net_b_eur": net_b, "difference": abs(diff)},
            confidence=0.85, amount_eur=abs(diff),
            suggested_action="Intercompany balances do not offset; agree the pair and book "
                             "the missing side."))
    return out


def _cutoff_findings(ds: Dataset) -> list[Finding]:
    pe = ds.period_end
    out = []
    for r in _records(ds, "invoices"):
        d, s = r["invoice_date"], r["service_period_end"]
        if not isinstance(d, date) or not isinstance(s, date):
            continue
        if d > pe and s <= pe:
            kind = "accrual"
            action = "Service received in the period but invoiced after it; accrue the expense."
        elif d <= pe and s > pe:
            kind = "deferral"
            action = "Invoice covers service after the period end; defer to prepaid."
        else:
            continue
        net = _plain(r["net_amount"])
        out.append(Finding(
            check="cutoff", category="gl", severity="medium", entity=str(r["entity"]),
            record_type="invoice", record_ids=[str(r["invoice_id"])],
            evidence={"kind": kind, "invoice_date": _iso(d), "service_period_end": _iso(s),
                      "period_end": _iso(pe), "net_amount": None if net is None else float(net)},
            confidence=0.8, suggested_action=action,
            amount_eur=0.0 if net is None else round(abs(float(net)), 2)))
    return out


def check_gl(ds: Dataset, policy: dict) -> list[Finding]:
    fx = FxRates(ds)
    out: list[Finding] = []
    if ds.has("gl_entries"):
        out += _je_findings(ds, fx) + _tb_findings(ds, fx) + _intercompany_findings(ds, fx)
    if ds.has("invoices"):
        out += _cutoff_findings(ds)
    return sorted(out, key=lambda f: f.finding_id)


def trial_balance(ds: Dataset) -> pd.DataFrame:
    if not ds.has("gl_entries"):
        return pd.DataFrame(columns=TB_COLUMNS)
    rows: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in _records(ds, "gl_entries"):
        key = (_text(r["entity"]) or "", _text(r["account"]) or "")
        rows[key][0] += _num(r["debit"])
        rows[key][1] += _num(r["credit"])
    data = [{"entity": e, "account": a, "debit": round(d, 2), "credit": round(c, 2),
             "balance": round(d - c, 2)}
            for (e, a), (d, c) in sorted(rows.items())]
    return pd.DataFrame(data, columns=TB_COLUMNS)
