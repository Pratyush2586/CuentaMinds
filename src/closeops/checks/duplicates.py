"""Duplicate invoice and duplicate payment checks.

Every pair of records yields at most one finding; for clusters of 3+ copies each
later copy is paired with the earliest one. record_ids = [later, earlier].
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from rapidfuzz import fuzz

from closeops.checks._util import _iso, _plain
from closeops.ingest.normalize import clean_iban, norm_invoice_no, norm_vendor
from closeops.model import Dataset, Finding

CENT = 0.005  # amounts are equal when they differ by less than half a cent
NEAR_TOLERANCE = 0.01


def _vendor_names(ds: Dataset) -> dict[str, str]:
    if not ds.has("vendors"):
        return {}
    vendors = ds.table("vendors")
    names: dict[str, str] = {}
    for vid, name in zip(vendors["vendor_id"], vendors["name"]):
        vid, name = _plain(vid), _plain(name)
        if vid is not None and str(vid) not in names:
            names[str(vid)] = norm_vendor(name)
    return names


def _invoice_records(ds: Dataset) -> list[dict]:
    names = _vendor_names(ds)
    out = []
    for r in ds.table("invoices").to_dict("records"):
        r = {k: _plain(v) for k, v in r.items()}
        net, gross = r["net_amount"], r["gross_amount"]
        if gross is None or float(gross) < 0 or (net is not None and float(net) < 0):
            continue  # credit notes / unusable rows
        vid = str(r["vendor_id"])
        out.append({
            "id": str(r["invoice_id"]),
            "entity": str(r["entity"]),
            "vendor_id": vid,
            "vendor_name": names.get(vid, ""),
            "no": norm_invoice_no(r["invoice_no"]),
            "raw_no": r["invoice_no"],
            "date": r["invoice_date"],
            "gross": float(gross),
        })
    return out


def _order(r: dict):
    """Chronological sort key (date, then id; undated records sort last)."""
    d = r["date"]
    return (d is None, d or date.min, r["id"])


def _same_vendor(a: dict, b: dict) -> bool:
    return a["vendor_id"] == b["vendor_id"] or (
        a["vendor_name"] != "" and a["vendor_name"] == b["vendor_name"])


def _classify(a: dict, b: dict, window: int, similarity: float) -> tuple[str, dict] | None:
    """Return (check, evidence extras) if the invoice pair is a duplicate, else None."""
    if not _same_vendor(a, b):
        return None
    same_no = a["no"] != "" and a["no"] == b["no"]
    gross_diff = abs(a["gross"] - b["gross"])
    days = abs((a["date"] - b["date"]).days) if a["date"] and b["date"] else None
    ratio = 100.0 if same_no else (fuzz.ratio(a["no"], b["no"]) if a["no"] and b["no"] else 0.0)
    is_near = (gross_diff <= NEAR_TOLERANCE and days is not None
               and days <= window and ratio >= similarity)
    is_exact = a["vendor_id"] == b["vendor_id"] and same_no and gross_diff < CENT
    extra = {"gross_diff": round(gross_diff, 2), "days_apart": days,
             "invoice_no_similarity": round(float(ratio), 1)}
    if a["entity"] != b["entity"]:
        return ("dup_cross_entity", extra) if (same_no or is_near) else None
    if is_exact:
        return "dup_exact", extra
    if is_near:
        return "dup_near", extra
    return None


_INVOICE_META = {
    "dup_exact": (0.99, "high", "Block payment of the later invoice; reverse it if already posted."),
    "dup_near": (0.8, "medium", "Review the later invoice against the earlier one before payment."),
    "dup_cross_entity": (0.9, "high", "Same vendor invoice booked in two entities; block the later "
                                      "one and confirm with both entities."),
}


def _candidate_pairs(recs: list[dict]):
    """Blocking: same normalised invoice number, or gross amounts within the near tolerance."""
    seen: set[tuple[str, str]] = set()
    by_no: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        if r["no"]:
            by_no[r["no"]].append(r)
    groups = list(by_no.values())
    by_gross = sorted(recs, key=lambda r: r["gross"])
    for i, a in enumerate(by_gross):
        for b in by_gross[i + 1:]:
            if b["gross"] - a["gross"] > NEAR_TOLERANCE:
                break
            groups.append([a, b])
    for g in groups:
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                key = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
                if key not in seen:
                    seen.add(key)
                    yield a, b


def _invoice_findings(ds: Dataset, policy: dict) -> list[Finding]:
    cfg = policy["duplicates"]
    window, similarity = cfg["date_window_days"], cfg["invoice_no_similarity"]
    best: dict[str, tuple[dict, dict, str, dict]] = {}  # later id -> (later, earliest, check, extra)
    for a, b in _candidate_pairs(_invoice_records(ds)):
        hit = _classify(a, b, window, similarity)
        if hit is None:
            continue
        later, earlier = (a, b) if _order(a) > _order(b) else (b, a)
        cur = best.get(later["id"])
        if cur is None or _order(earlier) < _order(cur[1]):
            best[later["id"]] = (later, earlier, hit[0], hit[1])
    out = []
    for later, earlier, check, extra in best.values():
        conf, sev, action = _INVOICE_META[check]
        out.append(Finding(
            check=check, category="duplicate", severity=sev, entity=later["entity"],
            record_type="invoice", record_ids=[later["id"], earlier["id"]],
            evidence={
                "vendor_id": later["vendor_id"], "earlier_vendor_id": earlier["vendor_id"],
                "invoice_no": _plain(later["raw_no"]), "earlier_invoice_no": _plain(earlier["raw_no"]),
                "invoice_date": _iso(later["date"]), "earlier_invoice_date": _iso(earlier["date"]),
                "gross_amount": later["gross"], "earlier_gross_amount": earlier["gross"],
                "entity": later["entity"], "earlier_entity": earlier["entity"],
                **extra,
            },
            confidence=conf, suggested_action=action, amount_eur=later["gross"],
        ))
    return out


def _payment_findings(ds: Dataset, policy: dict) -> list[Finding]:
    window = policy["duplicates"].get("payment_window_days", 3)
    by_iban: dict[str, list[dict]] = defaultdict(list)
    for r in ds.table("bank_transactions").to_dict("records"):
        r = {k: _plain(v) for k, v in r.items()}
        iban = clean_iban(r["counterparty_iban"])
        if r["amount"] is None or float(r["amount"]) >= 0 or iban is None or r["txn_date"] is None:
            continue
        by_iban[iban].append({
            "id": str(r["txn_id"]), "entity": str(r["entity"]), "iban": iban,
            "amount": float(r["amount"]), "date": r["txn_date"],
            "name": r["counterparty_name"], "currency": r["currency"]})
    out = []
    for group in by_iban.values():
        group.sort(key=_order)
        for i, later in enumerate(group):
            for earlier in group[:i]:  # chronological, so the first hit is the earliest
                days = (later["date"] - earlier["date"]).days
                if days <= window and abs(later["amount"] - earlier["amount"]) < CENT:
                    out.append(Finding(
                        check="dup_payment", category="duplicate", severity="high",
                        entity=later["entity"], record_type="bank_transaction",
                        record_ids=[later["id"], earlier["id"]],
                        evidence={
                            "counterparty_iban": later["iban"],
                            "counterparty_name": _plain(later["name"]),
                            "amount": later["amount"], "currency": _plain(later["currency"]),
                            "txn_date": _iso(later["date"]), "earlier_txn_date": _iso(earlier["date"]),
                            "days_apart": days, "entity": later["entity"],
                            "earlier_entity": earlier["entity"],
                        },
                        confidence=0.85,
                        suggested_action="Confirm the second payment was intended; "
                                         "recover the amount from the payee if not.",
                        amount_eur=abs(later["amount"]),
                    ))
                    break
    return out


def check_duplicates(ds: Dataset, policy: dict) -> list[Finding]:
    findings: list[Finding] = []
    if ds.has("invoices"):
        findings += _invoice_findings(ds, policy)
    if ds.has("bank_transactions"):
        findings += _payment_findings(ds, policy)
    return sorted(findings, key=lambda f: f.finding_id)
