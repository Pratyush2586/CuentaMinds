"""Remittance advices (txt e-mail, csv, xlsx, pdf) -> structured advices, format-agnostic.

Every advice is reduced to text lines; a line that carries amounts is a document line candidate
(document token + gross/deduction/paid + reason words); everything else feeds the header (payment refs, total).
The engine later resolves document tokens against the open items, so layout changes do not matter.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

AMOUNT = re.compile(r"(?<![\w/.,-])-?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2}(?![\d/])|(?<![\w/.,-])-?\d+[.,]\d{2}(?![\d/])")
DATE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}\b")
REF = re.compile(r"[A-Z0-9][A-Z0-9/\-_.]{5,}")


@dataclass
class Advice:
    file: str
    entity: str = ""
    payer: str = ""
    received: str = ""
    refs: list[str] = field(default_factory=list)
    total: float | None = None
    lines: list[dict] = field(default_factory=list)   # {"doc": str, "nums": [floats], "reason": str}


def amount(tok: str) -> float:
    t = tok.replace(" ", "")
    neg = t.startswith("-")
    t = t.lstrip("-")
    dec = t[-3]
    whole = re.sub(r"[.,]", "", t[:-3])
    v = float(f"{whole}.{t[-2:]}") if dec in ".," else float(re.sub(r"[.,]", "", t))
    return -v if neg else v


def file_text(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    if suf == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = []
                for v in row:
                    if v is None:
                        continue
                    if isinstance(v, float):
                        cells.append(f"{v:.2f}")
                    elif isinstance(v, int) and not isinstance(v, bool):  # whole amounts vs. order numbers
                        cells.append(f"{v}.00" if abs(v) < 1_000_000 else str(v))
                    elif hasattr(v, "isoformat"):
                        cells.append(v.isoformat()[:10])
                    else:
                        cells.append(str(v))
                out.append("  ".join(cells))
        return "\n".join(out)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if suf == ".csv":
        delim = ";" if txt.count(";") > txt.count(",") else ","
        return "\n".join("  ".join(c.strip() for c in row if c.strip()) for row in csv.reader(io.StringIO(txt), delimiter=delim))
    return txt


def parse(text: str) -> tuple[list[str], float | None, list[dict]]:
    refs, lines, total = [], [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        no_dates = DATE.sub(" ", up)
        nums = [amount(m.group(0)) for m in AMOUNT.finditer(no_dates)]
        words = REF.findall(AMOUNT.sub(" ", no_dates))
        if re.search(r"\b(AMOUNT|IMPORTE|BETRAG|MONTANT|TOTAL|SUMME|PAYMENT OF|PAGO DE|POR IMPORTE)\b", up) and nums:
            total = nums[-1] if total is None else total
        if nums and words:
            reason = " ".join(w for w in re.findall(r"[A-ZÀ-Ý][A-ZÀ-Ý.]{2,}", AMOUNT.sub(" ", no_dates))
                              if not re.search(r"\d", w))
            lines.append({"doc": words[0], "tokens": words, "nums": nums, "reason": reason})
        else:
            refs.extend(w for w in words if sum(ch.isdigit() for ch in w) >= 4)
    return refs, total, lines


def _one(args) -> dict:
    root, rel, meta = args
    a = Advice(file=rel, **meta)
    try:
        a.refs, a.total, a.lines = parse(file_text(Path(root) / rel))
    except Exception as exc:  # unreadable advice: keep it, empty
        a.refs = [f"__error__ {type(exc).__name__}"]
    return asdict(a)


def load_advices(dirs: list[Path], cache_dir: Path, min_received: str = "", workers: int = 16) -> list[dict]:
    """Parse every advice listed in <dir>/remittance_advices/index.csv (cached). dirs[0] is the phase;
    later dirs are earlier phases, of which only advices received on/after min_received are kept."""
    out, seen = [], set()
    for n, d in enumerate(dirs):
        ra = d / "remittance_advices"
        idx = ra / "index.csv"
        if not idx.exists():
            continue
        floor = min_received if n else ""
        cache = cache_dir / f"advices_{d.parent.name}_{d.name}_{floor or 'all'}.json"
        if cache.exists():
            parsed = json.loads(cache.read_text(encoding="utf-8"))
            out.extend(a for a in parsed if a["file"] not in seen)
            seen.update(a["file"] for a in parsed)
            continue
        rows = [r for r in csv.DictReader(open(idx, encoding="utf-8")) if r.get("received_date", "") >= floor
                and r["file"] not in seen]
        jobs = [(str(ra), r["file"], {"entity": r.get("entity", ""), "payer": r.get("payer_name", ""),
                                       "received": r.get("received_date", "")}) for r in rows
                if not os.path.basename(r["file"]).startswith("._")]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            parsed = list(pool.map(_one, jobs, chunksize=32))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(parsed), encoding="utf-8")
        out.extend(parsed)
        seen.update(a["file"] for a in parsed)
    return out
