"""Raw-file reading and markdown profiling of an unseen data directory."""
from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

import pandas as pd

from closeops.ingest.normalize import parse_amount, parse_date

log = logging.getLogger("closeops.ingest")

CSV_SUFFIXES = {".csv", ".tsv"}


def _sniff_delimiter(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    try:
        return csv.Sniffer().sniff(first, delimiters=",;\t").delimiter
    except csv.Error:
        return max(",;\t", key=first.count)


def _read_csv_text(text: str, path: Path) -> pd.DataFrame:
    """Parse csv text; short rows are padded, over-long rows skipped (index keeps record position)."""
    rows = list(csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text)))
    if not rows:
        raise ValueError("empty file")
    header, width = rows[0], len(rows[0])
    keep, idx = [], []
    for i, r in enumerate(rows[1:]):
        if len(r) > width:
            log.warning("%s: record %d has %d fields, expected %d; skipped", path.name, i + 2, len(r), width)
            continue
        keep.append(r + [""] * (width - len(r)))
        idx.append(i)
    return pd.DataFrame(keep, columns=header, index=idx, dtype=object).astype(str)


def read_raw(path: Path, sheet: str | None = None) -> pd.DataFrame:
    """Read a csv/xlsx/json file with every cell as a string ("" = missing).

    The returned index is the physical position: add 2 for csv/xlsx line numbers
    (header is line 1) or 1 for JSON record numbers.
    """
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp1252", errors="replace")
        df = _read_csv_text(text, path)
    elif suffix in (".xlsx", ".xlsm"):
        df = pd.read_excel(path, sheet_name=sheet if sheet else 0, dtype=str, keep_default_na=False)
    elif suffix == ".json":
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        df = pd.DataFrame([{str(k): "" if v is None else str(v) for k, v in r.items()}
                           for r in records], dtype=object).fillna("")
    else:
        raise ValueError(f"unsupported file type: {path}")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _guess_dtype(values: pd.Series) -> str:
    vals = [v for v in values if v != ""]
    if not vals:
        return "empty"
    for name, fn in (("date", parse_date), ("number", parse_amount)):
        if all(fn(v) is not None for v in vals):
            return name
    return "text"


def _profile_frame(df: pd.DataFrame) -> list[str]:
    n = len(df)
    lines = [f"{n} rows", "", "| column | dtype | null % | sample |", "|---|---|---|---|"]
    keys = []
    for c in df.columns:
        col = df[c]
        nulls = (col == "").sum()
        sample = ", ".join(repr(v) for v in col[col != ""].head(3))
        lines.append(f"| {c} | {_guess_dtype(col)} | {100 * nulls / n if n else 0:.0f} | {sample} |")
        if n and nulls == 0 and col.is_unique:
            keys.append(c)
    lines += ["", "Sample rows:", ""]
    for _, r in df.head(3).iterrows():
        lines.append("- " + "; ".join(f"{c}={v}" for c, v in r.items()))
    lines += ["", "Candidate key columns (100% unique): " + (", ".join(keys) if keys else "none")]
    return lines


def profile_dir(raw_dir: Path) -> str:
    raw_dir = Path(raw_dir)
    out = [f"# Profile of {raw_dir}", ""]
    pdfs = 0
    for path in sorted(p for p in raw_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(raw_dir).as_posix()
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                pdfs += 1
            elif suffix in (".xlsx", ".xlsm"):
                for sheet in pd.ExcelFile(path).sheet_names:
                    out += [f"## {rel} [sheet: {sheet}]", ""]
                    out += _profile_frame(read_raw(path, sheet)) + [""]
            elif suffix in CSV_SUFFIXES | {".json"}:
                out += [f"## {rel}", ""] + _profile_frame(read_raw(path)) + [""]
        except Exception as exc:  # profiling must never abort on one bad file
            out += [f"## {rel}", "", f"unreadable: {exc}", ""]
    out.append(f"PDF files: {pdfs}")
    return "\n".join(out) + "\n"
