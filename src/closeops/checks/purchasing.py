"""Purchasing checks: PO / goods-receipt / invoice (3-way) match and contract price.

Each invoice yields at most one finding, chosen by precedence
qty_over_received > po_price_mismatch > contract_price_mismatch > no_po; the winner's
evidence lists the other checks that also fired under `also_triggered`.
Credit notes (net_amount < 0) are skipped by every check.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd

from closeops.checks._util import _iso, _plain
from closeops.model import Dataset, Finding

_META = {
    "qty_over_received": ("high", 0.9,
                          "Invoiced quantity exceeds goods received; hold payment until receipt "
                          "is confirmed or the invoice is corrected."),
    "po_price_mismatch": ("medium", 0.85,
                          "Invoice unit price differs from the PO price; query the vendor or "
                          "amend the PO."),
    "contract_price_mismatch": ("medium", 0.85,
                                "Invoice unit price differs from the active contract price; "
                                "query the vendor."),
    "no_po": ("medium", 0.8,
              "No valid purchase order for this invoice; obtain PO approval or reject."),
}
PRECEDENCE = ["qty_over_received", "po_price_mismatch", "contract_price_mismatch", "no_po"]


def _s(v) -> str | None:
    v = _plain(v)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _f(v) -> float | None:
    v = _plain(v)
    return None if v is None else float(v)


def _records(ds: Dataset, name: str) -> list[dict]:
    if not ds.has(name):
        return []
    return [{k: _plain(v) for k, v in r.items()} for r in ds.table(name).to_dict("records")]


def _pct_diff(price: float | None, ref: float | None) -> float | None:
    if price is None or ref is None or ref == 0:
        return None
    return abs(price - ref) / ref * 100


def _key(v) -> str | None:
    s = _s(v)
    return s.lower() if s else None


class _Refs:
    def __init__(self, ds: Dataset):
        self.has_po = ds.has("purchase_orders")
        self.has_gr = ds.has("goods_receipts")
        self.has_contracts = ds.has("contracts")
        self.pos: dict[str, dict] = {}
        for r in _records(ds, "purchase_orders"):
            pid = _s(r["po_id"])
            if pid is not None and pid not in self.pos:
                self.pos[pid] = r
        self.received: dict[str, float] = defaultdict(float)
        self.receipt_rows: dict[str, int] = defaultdict(int)
        for r in _records(ds, "goods_receipts"):
            pid = _s(r["po_id"])
            if pid is None:
                continue
            self.receipt_rows[pid] += 1
            self.received[pid] += _f(r["qty_received"]) or 0.0
        self.contracts: list[dict] = sorted(_records(ds, "contracts"),
                                            key=lambda c: str(c["contract_id"]))

    def contracts_for(self, vendor_id, item, d: date | None) -> list[dict]:
        v, i = _s(vendor_id), _key(item)
        if v is None or i is None or d is None:
            return []
        out = []
        for c in self.contracts:
            if _s(c["vendor_id"]) != v or _key(c["item"]) != i:
                continue
            if c["valid_from"] is not None and d < c["valid_from"]:
                continue
            if c["valid_to"] is not None and d > c["valid_to"]:
                continue
            out.append(c)
        return out


def _is_credit(r: dict) -> bool:
    net = _f(r["net_amount"])
    return net is not None and net < 0


def _analyse(r: dict, refs: _Refs, tol_pct: float, qty_tol: float) -> dict:
    """Per-invoice facts: triggered checks (with evidence) and match status."""
    po_id = _s(r["po_id"])
    po = refs.pos.get(po_id) if po_id else None
    price, qty, d = _f(r["unit_price"]), _f(r["qty"]), r["invoice_date"]
    hits: dict[str, dict] = {}
    if refs.has_po and po is None:
        hits["no_po"] = {"reason": "po_id_missing" if po_id is None else "po_not_found"}
    if po is not None:
        po_price = _f(po["unit_price"])
        po_diff = _pct_diff(price, po_price)
        if po_diff is not None and po_diff > tol_pct:
            hits["po_price_mismatch"] = {"invoice_unit_price": price, "po_unit_price": po_price,
                                         "difference_pct": round(po_diff, 4),
                                         "tolerance_pct": tol_pct}
    no_receipt = po is not None and (not refs.has_gr or refs.receipt_rows.get(po_id, 0) == 0)
    qty_over = False
    if po is not None and not no_receipt and qty is not None:
        received = refs.received.get(po_id, 0.0)
        if qty > received + qty_tol:
            qty_over = True
            hits["qty_over_received"] = {"invoice_qty": qty, "qty_received": received,
                                         "qty_tolerance": qty_tol}
    if refs.has_contracts:
        matches = refs.contracts_for(r["vendor_id"], r["item"], d)
        diffs = [(_pct_diff(price, _f(c["unit_price"])), c) for c in matches]
        diffs = [(x, c) for x, c in diffs if x is not None]
        if diffs and all(x > tol_pct for x, _ in diffs):
            x, c = diffs[0]
            hits["contract_price_mismatch"] = {
                "invoice_unit_price": price, "contract_id": _s(c["contract_id"]),
                "contract_unit_price": _f(c["unit_price"]), "difference_pct": round(x, 4),
                "tolerance_pct": tol_pct}
    if po_id is None or (refs.has_po and po is None):
        status = "no_po"
    elif no_receipt:
        status = "no_receipt"
    elif qty_over:
        status = "qty_mismatch"
    elif "po_price_mismatch" in hits:
        status = "price_mismatch"
    else:
        status = "matched"
    return {"hits": hits, "status": status, "po_id": po_id}


def _params(policy: dict) -> tuple[float, float]:
    cfg = policy["match"]
    return float(cfg["price_tolerance_pct"]), float(cfg["qty_tolerance"])


def check_purchasing(ds: Dataset, policy: dict) -> list[Finding]:
    tol, qty_tol = _params(policy)
    refs = _Refs(ds)
    out = []
    for r in _records(ds, "invoices"):
        if _is_credit(r):
            continue
        hits = _analyse(r, refs, tol, qty_tol)["hits"]
        for check in PRECEDENCE:
            if check not in hits:
                continue
            sev, conf, action = _META[check]
            gross = _f(r["gross_amount"])
            evidence = {"vendor_id": _s(r["vendor_id"]), "invoice_no": _s(r["invoice_no"]),
                        "po_id": _s(r["po_id"]), "item": _s(r["item"]),
                        "invoice_date": _iso(r["invoice_date"]), **hits[check],
                        "also_triggered": [c for c in PRECEDENCE if c in hits and c != check]}
            out.append(Finding(check=check, category="match", severity=sev,
                               entity=str(r["entity"]), record_type="invoice",
                               record_ids=[str(r["invoice_id"])], evidence=evidence,
                               confidence=conf, suggested_action=action,
                               amount_eur=abs(gross) if gross is not None else 0.0))
            break
    return sorted(out, key=lambda f: f.finding_id)


def match_status(ds: Dataset, policy: dict) -> pd.DataFrame:
    tol, qty_tol = _params(policy)
    refs = _Refs(ds)
    rows = []
    for r in _records(ds, "invoices"):
        if _is_credit(r):
            rows.append({"invoice_id": str(r["invoice_id"]), "po_id": _s(r["po_id"]),
                         "status": "credit_note"})
            continue
        a = _analyse(r, refs, tol, qty_tol)
        rows.append({"invoice_id": str(r["invoice_id"]), "po_id": a["po_id"],
                     "status": a["status"]})
    return pd.DataFrame(rows, columns=["invoice_id", "po_id", "status"])
