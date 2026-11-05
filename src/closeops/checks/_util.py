"""Helpers shared by the rule-engine checks."""
from __future__ import annotations

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
