"""Invoice tax checks.

Each invoice yields at most one finding, chosen by precedence
tax_math > tax_amount > tax_reverse_charge > tax_rate > tax_missing_vat_id
(most specific / confident first). Credit notes are compared on absolute values.
"""
from __future__ import annotations

from closeops.checks._util import _plain
from closeops.model import Dataset, Finding

RATE_EPS = 1e-6
ZERO_KINDS = {"zero", "exempt"}

_META = {
    "tax_math": (0.99, "high", "Recompute the invoice totals with the vendor; net + tax must equal gross."),
    "tax_amount": (0.97, "high", "Tax does not equal net x rate; ask the vendor for a corrected invoice."),
    "tax_reverse_charge": (0.8, "medium", "Check reverse-charge treatment; "
                                          "request a corrected invoice or self-assess the VAT."),
    "tax_rate": (0.85, "medium", "Applied rate is not a valid rate for the buyer country; "
                                 "confirm the rate with the vendor."),
    "tax_missing_vat_id": (0.6, "low", "Vendor charged tax but has no VAT id on file; "
                                       "obtain it before reclaiming input VAT."),
}


def _num(v) -> float | None:
    v = _plain(v)
    return None if v is None else float(v)


def _text(v) -> str | None:
    v = _plain(v)
    if v is None:
        return None
    s = str(v).strip()
    return s.upper() or None


def _fraction(rate: float | None) -> float | None:
    """Rates arrive as 21 or 0.21; anything above 1 is a percentage."""
    if rate is None:
        return None
    return rate / 100 if rate > 1 else rate


def _rate_book(ds: Dataset) -> dict[str, dict]:
    """country -> {"rates": set of fractions, "standard": fraction|None, "zero_ok": bool}."""
    book: dict[str, dict] = {}
    if not ds.has("tax_rates"):
        return book
    for r in ds.table("tax_rates").to_dict("records"):
        country, rate = _text(r["country"]), _fraction(_num(r["rate"]))
        if country is None:
            continue
        kind = (_text(r["kind"]) or "").lower()
        e = book.setdefault(country, {"rates": set(), "standard": None, "zero_ok": False})
        if kind in ZERO_KINDS:  # a zero/exempt row counts even without a numeric rate
            e["zero_ok"] = True
            e["rates"].add(0.0)
        if rate is None:
            continue
        e["rates"].add(rate)
        if kind == "standard" and e["standard"] is None:
            e["standard"] = rate
    return book


def _vendors_without_vat(ds: Dataset) -> set[str] | None:
    """Vendor ids with no VAT id on any vendor row; None when there is no vendors table."""
    if not ds.has("vendors"):
        return None
    has_vat: dict[str, bool] = {}
    for vid, vat in zip(ds.table("vendors")["vendor_id"], ds.table("vendors")["vat_id"]):
        vid = _plain(vid)
        if vid is None:
            continue
        has_vat[str(vid)] = has_vat.get(str(vid), False) or _text(vat) is not None
    return {v for v, ok in has_vat.items() if not ok}


def _evaluate(r: dict, tol: float, eu: set[str], book: dict, no_vat: set[str] | None):
    """Return (check, expected_tax) for the first check the invoice fails, else None."""
    net, tax, gross = r["net"], r["tax"], r["gross"]
    rate, vc, bc = r["rate"], r["vc"], r["bc"]
    if net is not None and tax is not None and gross is not None and abs(net + tax - gross) > tol:
        return "tax_math", gross - net
    if net is not None and rate is not None and tax is not None and abs(net * rate - tax) > tol:
        return "tax_amount", net * rate
    country = book.get(bc) if bc else None
    standard = country["standard"] if country else None
    intra_eu = vc is not None and bc is not None and vc != bc and vc in eu and bc in eu
    if tax is not None:
        if intra_eu and abs(tax) > tol:
            return "tax_reverse_charge", 0.0
        zero_ok = bool(country and country["zero_ok"] and rate is not None and rate <= RATE_EPS)
        if (vc is not None and vc == bc and abs(tax) <= tol and standard and standard > 0
                and net is not None and not zero_ok):
            return "tax_reverse_charge", net * standard
    non_eu_untaxed = vc is not None and vc not in eu and tax is not None and tax <= tol
    if country and not intra_eu and not non_eu_untaxed and rate is not None:
        if not any(abs(rate - x) <= RATE_EPS for x in country["rates"]):
            return "tax_rate", (net * standard if net is not None and standard is not None else None)
    if no_vat is not None and r["vendor_id"] in no_vat and tax is not None and tax > tol:
        return "tax_missing_vat_id", 0.0
    return None


def check_tax(ds: Dataset, policy: dict) -> list[Finding]:
    if not ds.has("invoices"):
        return []
    cfg = policy["tax"]
    tol = float(cfg["tolerance"])
    eu = {str(c).upper() for c in cfg["eu_countries"]}
    book = _rate_book(ds)
    no_vat = _vendors_without_vat(ds)
    out = []
    for row in ds.table("invoices").to_dict("records"):
        row = {k: _plain(v) for k, v in row.items()}
        r = {
            "id": str(row["invoice_id"]), "entity": str(row["entity"]),
            "vendor_id": None if row["vendor_id"] is None else str(row["vendor_id"]),
            "net": _abs(_num(row["net_amount"])), "tax": _abs(_num(row["tax_amount"])),
            "gross": _abs(_num(row["gross_amount"])), "rate": _fraction(_num(row["tax_rate"])),
            "vc": _text(row["vendor_country"]), "bc": _text(row["buyer_country"]),
        }
        hit = _evaluate(r, tol, eu, book, no_vat)
        if hit is None:
            continue
        check, expected = hit
        booked = r["tax"]
        diff = None if expected is None or booked is None else round(abs(booked - expected), 2)
        conf, sev, action = _META[check]
        out.append(Finding(
            check=check, category="tax", severity=sev, entity=r["entity"],
            record_type="invoice", record_ids=[r["id"]],
            evidence={
                "expected_tax": None if expected is None else round(expected, 2),
                "booked_tax": booked, "difference": diff,
                "net_amount": r["net"], "gross_amount": r["gross"], "tax_rate": r["rate"],
                "vendor_country": r["vc"], "buyer_country": r["bc"], "vendor_id": r["vendor_id"],
                "invoice_no": _plain(row["invoice_no"]), "currency": _plain(row["currency"]),
            },
            confidence=conf, suggested_action=action,
            amount_eur=diff if diff is not None else (booked or 0.0),
        ))
    return sorted(out, key=lambda f: f.finding_id)


def _abs(v: float | None) -> float | None:
    return None if v is None else abs(v)
