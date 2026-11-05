"""YAML-driven loader: maps arbitrary source columns onto the canonical tables.

A table entry is one spec or a list of specs (e.g. one per bank file). A spec has
`files`, optional `sheet`, `skip_rows`, `columns`, `defaults`, `date_order`,
`amount_from: {debit, credit}`, `sign_col` + `sign_debit_values` and `id_prefix_entity`.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from closeops.ingest.normalize import clean_iban, parse_amount, parse_date
from closeops.ingest.profile import read_raw
from closeops.model import CANONICAL, PROVENANCE, Dataset

log = logging.getLogger("closeops.ingest")

AMOUNT_COLS = {"qty", "unit_price", "net_amount", "tax_rate", "tax_amount", "gross_amount",
               "amount", "debit", "credit", "rate_to_eur", "rate", "qty_received",
               "payment_terms_days"}
DATE_COLS = {"changed_at", "valid_from", "valid_to", "service_period_end", "date"}
IBAN_COLS = {"iban", "counterparty_iban"}
DATE_ORDERS = ("dmy", "mdy")
# ids that must be unique across entities, and the id columns id_prefix_entity rewrites
UNIQUE_IDS = {"invoices": "invoice_id", "bank_transactions": "txn_id", "gl_entries": "je_id",
              "purchase_orders": "po_id"}
PREFIX_COLS = {"invoices": ["invoice_id", "po_id"], "purchase_orders": ["po_id"],
               "goods_receipts": ["po_id"], "bank_transactions": ["txn_id"], "gl_entries": ["je_id"]}


def _kind(col: str) -> str:
    if col in AMOUNT_COLS:
        return "amount"
    if col in DATE_COLS or col.endswith("_date"):
        return "date"
    if col in IBAN_COLS:
        return "iban"
    return "text"


def _convert(series: pd.Series, kind: str, date_order: str = "dmy") -> pd.Series:
    if kind == "amount":
        return series.map(parse_amount).astype(float)
    if kind == "date":
        fn = lambda v: parse_date(v, date_order)  # noqa: E731
    else:
        fn = {"iban": clean_iban}.get(kind, lambda v: v if v != "" else None)
    return series.map(fn).astype(object).map(lambda v: pd.NA if v is None else v)


def _count(unparsed: dict, key: str, source: pd.Series, parsed: pd.Series) -> None:
    filled = source != ""
    unparsed[key][0] += int((filled & parsed.isna()).sum())
    unparsed[key][1] += int(filled.sum())


def _amount_from(debit: pd.Series, credit: pd.Series) -> pd.Series:
    """credit - debit (money in positive); a missing side counts 0, both missing -> NaN."""
    d, c = [parse_amount(v) for v in debit], [parse_amount(v) for v in credit]
    out = [None if (x is None and y is None) else (y or 0.0) - (x or 0.0) for x, y in zip(d, c)]
    return pd.Series(out, index=debit.index, dtype=float)


def _row_entity(name: str, out: pd.DataFrame, raw: pd.DataFrame, spec: dict) -> pd.Series | None:
    if "entity" in CANONICAL[name]:
        return out["entity"]
    src = (spec.get("columns") or {}).get("entity")
    if src is not None and src in raw.columns:
        return raw[src].map(lambda v: v or pd.NA)
    if "entity" in (spec.get("defaults") or {}):
        return pd.Series([str(spec["defaults"]["entity"])] * len(raw), index=raw.index)
    return None


def _prefix_ids(name: str, out: pd.DataFrame, raw: pd.DataFrame, spec: dict, path: Path,
                notes: list[str]) -> None:
    ent = _row_entity(name, out, raw, spec)
    if ent is None:
        msg = (f"{name}: id_prefix_entity needs an entity (columns.entity or defaults.entity) "
               f"in {path.name}; ids left unprefixed")
        log.warning(msg)
        notes.append(msg)
        return
    for col in PREFIX_COLS.get(name, []):
        out[col] = [v if pd.isna(v) or pd.isna(e) else f"{e}:{v}" for e, v in zip(ent, out[col])]


def _load_table(raw_dir: Path, name: str, spec: dict, date_order: str = "dmy",
                unparsed: dict | None = None, notes: list[str] | None = None) -> pd.DataFrame | None:
    if name not in CANONICAL:
        raise ValueError(f"mapping names unknown canonical table: {name}")
    unparsed = unparsed if unparsed is not None else defaultdict(lambda: [0, 0])
    notes = notes if notes is not None else []
    patterns = spec.get("files") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    files = sorted({p for pat in patterns for p in raw_dir.glob(pat) if p.is_file()})
    if not files:
        log.warning("table %s: no files match %s; skipped", name, patterns)
        return None
    columns = spec.get("columns") or {}
    defaults = spec.get("defaults") or {}
    skip_rows = int(spec.get("skip_rows") or 0)
    amount_from = spec.get("amount_from") or {}
    sign_col = spec.get("sign_col")
    debit_marks = {str(v).strip().upper() for v in spec.get("sign_debit_values") or []}
    canon = CANONICAL[name]
    parts = []
    for path in files:
        try:
            raw = read_raw(path, spec.get("sheet"), skip_rows)
        except Exception as exc:
            log.warning("table %s: skipping file %s: %s", name, path.name, exc)
            continue
        raw = raw.loc[(raw != "").any(axis=1)] if len(raw) else raw
        offset = 1 if path.suffix.lower() == ".json" else 2 + skip_rows
        out = pd.DataFrame(index=raw.index)
        used = set()
        for col in canon:
            src = columns.get(col)
            if col == "amount" and amount_from:
                srcs = [amount_from.get("debit"), amount_from.get("credit")]
                missing = [s for s in srcs if s not in raw.columns]
                if missing:
                    log.warning("table %s: amount_from column(s) %s not in %s", name, missing, path.name)
                    src = None
                else:
                    used.update(srcs)
                    out[col] = _amount_from(raw[srcs[0]], raw[srcs[1]])
                    for s in srcs:
                        _count(unparsed, f"{name}.{col}", raw[s], raw[s].map(parse_amount))
                    continue
            if src is not None and src not in raw.columns:
                log.warning("table %s: source column %r not in %s", name, src, path.name)
                src = None
            if src is not None:
                used.add(src)
                out[col] = _convert(raw[src], _kind(col), date_order)
                if _kind(col) in ("amount", "date"):
                    _count(unparsed, f"{name}.{col}", raw[src], out[col])
            elif col in defaults:
                out[col] = _convert(pd.Series([str(defaults[col])] * len(raw), index=raw.index),
                                    _kind(col), date_order)
            else:
                out[col] = _convert(pd.Series([""] * len(raw), index=raw.index), _kind(col))
        if sign_col and "amount" in out.columns:
            if sign_col in raw.columns:
                debit = raw[sign_col].map(lambda v: str(v).strip().upper() in debit_marks)
                out["amount"] = out["amount"].where(~debit, -out["amount"].abs())
            else:
                log.warning("table %s: sign_col %r not in %s", name, sign_col, path.name)
        if spec.get("id_prefix_entity"):
            _prefix_ids(name, out, raw, spec, path, notes)
        for c in raw.columns:
            if c not in used:
                out[f"x_{c}"] = _convert(raw[c], "text")
        out["source_file"] = path.relative_to(raw_dir).as_posix()
        out["source_row"] = raw.index + offset
        parts.append(out)
    if not parts:
        log.warning("table %s: no readable files; skipped", name)
        return None
    return pd.concat(parts, ignore_index=True)


def _specs(entry) -> list[dict]:
    if isinstance(entry, list):
        return [s or {} for s in entry]
    return [entry or {}]


def _repeated_id_notes(tables: dict[str, pd.DataFrame]) -> list[str]:
    notes = []
    for name, col in UNIQUE_IDS.items():
        df = tables.get(name)
        if df is None or df.empty:
            continue
        sub = df[[col, "entity"]].dropna()
        per_id = sub.groupby(sub[col].astype(str))["entity"].nunique()
        dup = sorted(per_id[per_id > 1].index)
        if dup:
            msg = (f"{name}.{col}: {len(dup)} ids repeat across entities "
                   f"(e.g. {', '.join(dup[:3])}); set id_prefix_entity: true")
            log.warning(msg)
            notes.append(msg)
    return notes


def load_dataset(raw_dir: Path, mapping_path: Path) -> Dataset:
    raw_dir = Path(raw_dir)
    cfg = yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8"))
    pe = cfg["period_end"]
    if isinstance(pe, datetime):
        pe = pe.date()
    elif not isinstance(pe, date):
        pe = date.fromisoformat(str(pe))
    base_order = cfg.get("date_order", "dmy")
    tables, notes = {}, []
    for name, entry in (cfg.get("tables") or {}).items():
        unparsed: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        parts = []
        for spec in _specs(entry):
            order = spec.get("date_order", base_order)
            if order not in DATE_ORDERS:
                raise ValueError(f"date_order must be one of {DATE_ORDERS}, got {order!r}")
            df = _load_table(raw_dir, name, spec, order, unparsed, notes)
            if df is not None:
                parts.append(df)
        for key, (bad, filled) in unparsed.items():
            if bad:
                msg = f"{key}: {bad}/{filled} unparsed"
                log.warning(msg)
                notes.append(msg)
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True)
        first = CANONICAL[name] + PROVENANCE  # canonical + provenance first, extras after
        tables[name] = df[first + [c for c in df.columns if c not in first]]
    notes += _repeated_id_notes(tables)
    return Dataset(tables=tables, period_end=pe, notes=notes)
