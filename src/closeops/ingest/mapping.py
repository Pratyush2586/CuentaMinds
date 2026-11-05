"""YAML-driven loader: maps arbitrary source columns onto the canonical tables."""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from closeops.ingest.normalize import clean_iban, parse_amount, parse_date
from closeops.ingest.profile import read_raw
from closeops.model import CANONICAL, PROVENANCE, Dataset

log = logging.getLogger("closeops.ingest")

AMOUNT_COLS = {"qty", "unit_price", "net_amount", "tax_rate", "tax_amount", "gross_amount",
               "amount", "debit", "credit", "rate_to_eur", "rate"}
DATE_COLS = {"changed_at", "valid_from", "valid_to", "service_period_end", "date"}
IBAN_COLS = {"iban", "counterparty_iban"}


def _kind(col: str) -> str:
    if col in AMOUNT_COLS:
        return "amount"
    if col in DATE_COLS or col.endswith("_date"):
        return "date"
    if col in IBAN_COLS:
        return "iban"
    return "text"


def _convert(series: pd.Series, kind: str) -> pd.Series:
    if kind == "amount":
        return series.map(parse_amount).astype(float)
    fn = {"date": parse_date, "iban": clean_iban}.get(kind, lambda v: v if v != "" else None)
    return series.map(fn).astype(object).map(lambda v: pd.NA if v is None else v)


def _load_table(raw_dir: Path, name: str, spec: dict) -> pd.DataFrame | None:
    if name not in CANONICAL:
        raise ValueError(f"mapping names unknown canonical table: {name}")
    patterns = spec.get("files") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    files = sorted({p for pat in patterns for p in raw_dir.glob(pat) if p.is_file()})
    if not files:
        log.warning("table %s: no files match %s; skipped", name, patterns)
        return None
    columns = spec.get("columns") or {}
    defaults = spec.get("defaults") or {}
    canon = CANONICAL[name]
    parts = []
    for path in files:
        try:
            raw = read_raw(path, spec.get("sheet"))
        except Exception as exc:
            log.warning("table %s: skipping file %s: %s", name, path.name, exc)
            continue
        raw = raw.loc[(raw != "").any(axis=1)] if len(raw) else raw
        offset = 1 if path.suffix.lower() == ".json" else 2
        out = pd.DataFrame(index=raw.index)
        used = set()
        for col in canon:
            src = columns.get(col)
            if src is not None and src not in raw.columns:
                log.warning("table %s: source column %r not in %s", name, src, path.name)
                src = None
            if src is not None:
                used.add(src)
                out[col] = _convert(raw[src], _kind(col))
            elif col in defaults:
                out[col] = _convert(pd.Series([str(defaults[col])] * len(raw), index=raw.index), _kind(col))
            else:
                out[col] = _convert(pd.Series([""] * len(raw), index=raw.index), _kind(col))
        for c in raw.columns:
            if c not in used:
                out[f"x_{c}"] = _convert(raw[c], "text")
        out["source_file"] = path.relative_to(raw_dir).as_posix()
        out["source_row"] = raw.index + offset
        parts.append(out)
    if not parts:
        log.warning("table %s: no readable files; skipped", name)
        return None
    df = pd.concat(parts, ignore_index=True)
    # keep canonical + provenance first, extras after
    first = canon + PROVENANCE
    return df[first + [c for c in df.columns if c not in first]]


def load_dataset(raw_dir: Path, mapping_path: Path) -> Dataset:
    raw_dir = Path(raw_dir)
    cfg = yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8"))
    pe = cfg["period_end"]
    if isinstance(pe, datetime):
        pe = pe.date()
    elif not isinstance(pe, date):
        pe = date.fromisoformat(str(pe))
    tables = {}
    for name, spec in (cfg.get("tables") or {}).items():
        df = _load_table(raw_dir, name, spec or {})
        if df is not None:
            tables[name] = df
    return Dataset(tables=tables, period_end=pe)
