"""Helpers shared by the rule-engine checks."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date

import pandas as pd


def _plain(v):
    """Pandas/numpy scalar -> plain python value (None for missing)."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item"):
        v = v.item()
    return v


def _iso(d) -> str | None:
    return d.isoformat() if isinstance(d, date) else None


class FxRates:
    """fx_rates lookups: rate_to_eur on the nearest date on or before a given day."""

    def __init__(self, ds):
        rows: dict[str, list[tuple[date, float]]] = defaultdict(list)
        if ds.has("fx_rates"):
            for r in ds.table("fx_rates").to_dict("records"):
                d, rate, cur = _plain(r["date"]), _plain(r["rate_to_eur"]), _plain(r["currency"])
                if isinstance(d, date) and rate is not None:
                    cur = str(cur).strip().upper() if cur is not None and str(cur).strip() else "EUR"
                    rows[cur].append((d, float(rate)))
        self.rates = {c: sorted(v) for c, v in rows.items()}

    def rate(self, currency: str, d: date | None) -> float | None:
        """rate_to_eur on the nearest date on or before `d`; EUR is 1.0 when not listed."""
        series = self.rates.get(currency)
        if not series:
            return 1.0 if currency == "EUR" else None
        if d is None:
            return None
        i = bisect_right(series, (d, float("inf")))
        return series[i - 1][1] if i else None
