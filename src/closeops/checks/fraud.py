"""Fraud / bank-change checks.

Each outgoing bank line yields at most one non-soft finding, chosen by precedence
iban_changed_before_payment > iban_not_in_master > iban_country_mismatch; the winner's
evidence lists the other checks that also fired under `also_triggered`.
Soft signals (round_amount, weekend_posting) are always returned and bypass precedence;
the exporter decides whether to drop them.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from closeops.checks._util import _iso, _plain
from closeops.ingest.normalize import clean_iban, norm_vendor
from closeops.model import Dataset, Finding

_META = {
    "iban_changed_before_payment": ("high", 0.9,
                                    "Hold the payment and verify the bank change with the vendor "
                                    "through a known contact (call-back)."),
    "iban_not_in_master": ("high", 0.85,
                           "Hold the payment; the IBAN matches no vendor master or bank history."),
    "iban_country_mismatch": ("medium", 0.7,
                              "Confirm the vendor's bank country before releasing the payment."),
    "split_invoices": ("medium", 0.7,
                       "Review whether the invoices were split to stay under the approval limit."),
    "shared_iban": ("high", 0.85,
                    "Investigate vendors sharing one bank account (possible duplicate or fake vendor)."),
}
_SOFT_ACTION = {
    "round_amount": "Informational: large round-number outgoing payment.",
    "weekend_posting": "Informational: journal entry posted on a weekend.",
}
PRECEDENCE = ["iban_changed_before_payment", "iban_not_in_master", "iban_country_mismatch"]


def _s(v) -> str | None:
    v = _plain(v)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _iban(v) -> str | None:
    return clean_iban(_plain(v))


def _records(ds: Dataset, name: str) -> list[dict]:
    if not ds.has(name):
        return []
    return [{k: _plain(v) for k, v in r.items()} for r in ds.table(name).to_dict("records")]


class _Vendors:
    """Vendor master lookups: country, current/historic IBANs, name index."""

    def __init__(self, ds: Dataset):
        self.present = ds.has("vendors")
        self.country: dict[str, str | None] = {}
        self.current: dict[str, set[str]] = defaultdict(set)   # iban -> vendor ids (current)
        self.by_iban: dict[str, set[str]] = defaultdict(set)   # iban -> vendor ids (current or historic)
        self.by_name: dict[str, str] = {}
        self.ids_by_name: dict[str, set[str]] = defaultdict(set)  # normalised name -> vendor ids
        self.current_iban: dict[str, str | None] = {}
        for r in _records(ds, "vendors"):
            vid = _s(r["vendor_id"])
            if vid is None:
                continue
            if vid not in self.country:
                c = _s(r["country"])
                self.country[vid] = c.upper() if c else None
                self.current_iban[vid] = _iban(r["iban"])
            iban = _iban(r["iban"])
            if iban:
                self.current[iban].add(vid)
                self.by_iban[iban].add(vid)
            n = norm_vendor(_s(r["name"]))
            if n:
                self.by_name.setdefault(n, vid)
                self.ids_by_name[n].add(vid)
        self.history: dict[str, list[dict]] = defaultdict(list)  # vendor -> changes, chronological
        for r in _records(ds, "vendor_bank_history"):
            vid, iban = _s(r["vendor_id"]), _iban(r["iban"])
            if vid is None or iban is None:
                continue
            self.history[vid].append({"iban": iban, "changed_at": r["changed_at"],
                                      "changed_by": _s(r["changed_by"])})
            self.by_iban[iban].add(vid)
        for rows in self.history.values():  # stable: ties keep file order
            rows.sort(key=lambda h: (h["changed_at"] is None, h["changed_at"] or date.min))

    def link(self, iban: str, name: str | None) -> str | None:
        ids = self.by_iban.get(iban)
        if ids:
            return sorted(ids)[0]
        n = norm_vendor(name)
        return self.by_name.get(n) if n else None

    def linked_vendors(self, iban: str | None, name: str | None) -> set[str]:
        """All vendors owning the counterparty IBAN (current or historic) or name."""
        out = set(self.by_iban.get(iban, ())) if iban else set()
        n = norm_vendor(name)
        return out | self.ids_by_name.get(n, set()) if n else out

    def old_iban(self, vid: str, idx: int) -> str | None:
        rows = self.history[vid]
        if idx > 0:
            return rows[idx - 1]["iban"]
        cur = self.current_iban.get(vid)
        return cur if cur != rows[idx]["iban"] else None


def _changed_before(v: _Vendors, iban: str, txn_date: date | None, window: int) -> dict | None:
    """Most recent bank change to `iban` within [0, window] days before the payment."""
    if txn_date is None:
        return None
    best = None
    for vid, rows in v.history.items():
        for i, h in enumerate(rows):
            if h["iban"] != iban or h["changed_at"] is None:
                continue
            days = (txn_date - h["changed_at"]).days
            if 0 <= days <= window and (best is None or days < best["days_between"]):
                best = {"vendor_id": vid, "old_iban": v.old_iban(vid, i), "new_iban": iban,
                        "changed_by": h["changed_by"], "changed_at": _iso(h["changed_at"]),
                        "days_between": days}
    return best


def _bank_findings(ds: Dataset, v: _Vendors, window: int) -> list[Finding]:
    out = []
    for r in _records(ds, "bank_transactions"):
        amount = r["amount"]
        iban = _iban(r["counterparty_iban"])
        if amount is None or float(amount) >= 0 or iban is None:
            continue
        tid, entity, amt = str(r["txn_id"]), str(r["entity"]), abs(float(amount))
        vid = v.link(iban, _s(r["counterparty_name"]))
        base = {"iban": iban, "vendor_id": vid, "txn_date": _iso(r["txn_date"]),
                "currency": _s(r["currency"]), "counterparty_name": _s(r["counterparty_name"])}
        hits: dict[str, dict] = {}
        change = _changed_before(v, iban, r["txn_date"], window)
        if change:
            hits["iban_changed_before_payment"] = change
        if v.present and iban not in v.by_iban:
            hits["iban_not_in_master"] = {}
        vcountry = v.country.get(vid) if vid else None
        if vcountry and len(iban) >= 2 and iban[:2] != vcountry:
            hits["iban_country_mismatch"] = {"iban_country": iban[:2], "vendor_country": vcountry}
        for check in PRECEDENCE:
            if check not in hits:
                continue
            sev, conf, action = _META[check]
            evidence = {**base, **hits[check],
                        "also_triggered": [c for c in PRECEDENCE if c in hits and c != check]}
            out.append(Finding(check=check, category="fraud", severity=sev, entity=entity,
                               record_type="bank_transaction", record_ids=[tid],
                               evidence=evidence, confidence=conf, suggested_action=action,
                               amount_eur=amt))
            break
    return out


def _shared_iban_findings(ds: Dataset, v: _Vendors) -> list[Finding]:
    entity_of: dict[str, str] = {}
    for r in _records(ds, "vendors"):
        vid = _s(r["vendor_id"])
        if vid is not None and vid not in entity_of:
            entity_of[vid] = str(r["entity"])
    out = []
    for iban, vids in v.current.items():
        if len(vids) < 2:
            continue
        ids = sorted(vids)
        sev, conf, action = _META["shared_iban"]
        out.append(Finding(check="shared_iban", category="fraud", severity=sev,
                           entity=entity_of[ids[0]], record_type="vendor", record_ids=ids,
                           evidence={"iban": iban, "vendor_ids": ids},
                           confidence=conf, suggested_action=action))
    return out


def _split_findings(ds: Dataset, limit: float, window: int) -> list[Finding]:
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for r in _records(ds, "invoices"):
        net, gross, d, vid = r["net_amount"], r["gross_amount"], r["invoice_date"], _s(r["vendor_id"])
        if vid is None or d is None or net is None or gross is None:
            continue
        if float(net) <= 0 or float(gross) >= limit:
            continue
        by_vendor[vid].append({"id": str(r["invoice_id"]), "date": d, "gross": float(gross),
                               "entity": str(r["entity"]), "no": _s(r["invoice_no"])})
    out = []
    for vid, rows in by_vendor.items():
        rows.sort(key=lambda x: (x["date"], x["id"]))
        runs, cur = [], []
        for x in rows:
            if cur and (x["date"] - cur[-1]["date"]).days > window:
                runs.append(cur)
                cur = []
            cur.append(x)
        runs.append(cur)
        for run in runs:
            total = sum(x["gross"] for x in run)
            if len(run) < 2 or total < limit:
                continue
            latest_first = sorted(run, key=lambda x: (x["date"], x["id"]), reverse=True)
            sev, conf, action = _META["split_invoices"]
            out.append(Finding(
                check="split_invoices", category="fraud", severity=sev,
                entity=latest_first[0]["entity"], record_type="invoice",
                record_ids=[x["id"] for x in latest_first],
                evidence={"vendor_id": vid, "total_gross": round(total, 2), "approval_limit": limit,
                          "invoice_count": len(run),
                          "invoice_dates": [_iso(x["date"]) for x in latest_first],
                          "invoice_nos": [x["no"] for x in latest_first]},
                confidence=conf, suggested_action=action, amount_eur=round(total, 2)))
    return out


def _soft_findings(ds: Dataset, round_min: float) -> list[Finding]:
    out = []
    for r in _records(ds, "bank_transactions"):
        amount = r["amount"]
        if amount is None or float(amount) >= 0:
            continue
        amt = abs(float(amount))
        if amt >= round_min and amt % 1000 == 0:
            out.append(Finding(
                check="round_amount", category="fraud", severity="low", entity=str(r["entity"]),
                record_type="bank_transaction", record_ids=[str(r["txn_id"])],
                evidence={"amount": amt, "txn_date": _iso(r["txn_date"]),
                          "counterparty_name": _s(r["counterparty_name"])},
                confidence=0.4, suggested_action=_SOFT_ACTION["round_amount"], amount_eur=amt))
    seen: dict[str, dict] = {}
    for r in _records(ds, "gl_entries"):
        d = r["posting_date"]
        if not isinstance(d, date) or d.weekday() < 5:
            continue
        jid = str(r["je_id"])
        e = seen.setdefault(jid, {"entity": str(r["entity"]), "date": d, "amount": 0.0})
        e["amount"] += float(r["debit"] or 0.0)
    for jid, e in seen.items():
        out.append(Finding(
            check="weekend_posting", category="fraud", severity="low", entity=e["entity"],
            record_type="gl_entry", record_ids=[jid],
            evidence={"posting_date": _iso(e["date"]), "weekday": e["date"].strftime("%A")},
            confidence=0.4, suggested_action=_SOFT_ACTION["weekend_posting"],
            amount_eur=round(e["amount"], 2)))
    return out


def check_fraud(ds: Dataset, policy: dict) -> list[Finding]:
    cfg = policy["fraud"]
    v = _Vendors(ds)
    out = _bank_findings(ds, v, int(cfg["iban_change_window_days"]))
    out += _shared_iban_findings(ds, v)
    out += _split_findings(ds, float(cfg["approval_limit"]), int(cfg["split_window_days"]))
    out += _soft_findings(ds, float(cfg["round_amount_min"]))
    return sorted(out, key=lambda f: f.finding_id)
