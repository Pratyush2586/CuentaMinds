"""Pure value normalisers applied before data enters the canonical tables."""
from __future__ import annotations

import re
from datetime import date, datetime

_LEGAL_SUFFIXES = {"sl", "sa", "gmbh", "ltd", "inc", "bv", "srl", "sas", "llc"}


def _is_missing(v) -> bool:
    if v is None:
        return True
    try:
        return bool(v != v)  # NaN / NaT
    except (TypeError, ValueError):
        return False


def parse_amount(v) -> float | None:
    """Parse European/US formatted numbers; None for missing or unparseable."""
    if _is_missing(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d.,+-]", "", s)  # drops currency symbols, spaces, parens
    if s.startswith("-"):
        negative = True
    s = s.lstrip("+-")
    if not s or not re.search(r"\d", s) or "-" in s or "+" in s:
        return None
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        if s.count(",") == 1 and re.fullmatch(r"\d*,\d{2}", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def parse_date(v) -> date | None:
    """Parse to a date; strings are day-first unless year-first. None if unparseable."""
    if _is_missing(v):
        return None
    if isinstance(v, datetime):  # includes pandas Timestamp
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s].*)?$", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[T\s].*)?$", s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def clean_iban(v) -> str | None:
    """Uppercase, whitespace-free IBAN; None if empty."""
    if _is_missing(v):
        return None
    s = re.sub(r"\s+", "", str(v)).upper()
    return s or None


def norm_vendor(name) -> str:
    """Casefolded vendor name without punctuation and trailing legal suffixes."""
    if _is_missing(name):
        return ""
    s = re.sub(r"[^\w\s]", "", str(name).casefold())
    tokens = s.split()
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def norm_invoice_no(v) -> str:
    """Uppercase alphanumerics with leading zeros of the trailing digit run removed."""
    if _is_missing(v):
        return ""
    s = re.sub(r"[^A-Za-z0-9]", "", str(v)).upper()
    m = re.search(r"\d+$", s)
    if not m:
        return s
    digits = m.group().lstrip("0") or "0"
    return s[: m.start()] + digits
