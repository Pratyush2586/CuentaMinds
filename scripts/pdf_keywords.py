"""List distinct clause templates (digits -> #) that contain payment-term keywords, across languages.

    python scripts/pdf_keywords.py [N=1500] [TYPES=invoice,credit_note]
"""
from __future__ import annotations

import collections
import csv
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data" / "valtermia" / "train" / "train" / "pdfs"
KEY = re.compile(r"instal|plazo|cuota|venci|reten|garant|anula|sustitu|annul|rempla|ersetz|storn|Rate|Raten|acompte|"
                 r"Abschlag|anticip|advance|deduct|descont|prestaç|parcel|échéan|scaden|ritenut|sostitu|cancel|offset|"
                 r"compens|refund|reembols|rembours|erstatt|verrechn|Einbehalt|Sicherheit|liberat|accept", re.I)


def keys(path: str) -> list[str]:
    try:
        with pdfplumber.open(path) as pdf:
            txt = " ".join((p.extract_text() or "").replace("\n", " ") for p in pdf.pages)
    except Exception:
        return []
    sents = re.split(r"(?<=[.!])\s+", txt)
    return [re.sub(r"\d[\d.,/ -]*\d|\d", "#", s.strip())[:200] for s in sents if KEY.search(s) and "sint" not in s]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    types = (sys.argv[2] if len(sys.argv) > 2 else "invoice,credit_note").split(",")
    rows = [r for r in csv.DictReader(open(D / "index.csv", encoding="utf-8")) if r["document_type"] in types]
    random.seed(7)
    smp = [str(D / r["file"]) for r in random.sample(rows, min(n, len(rows)))]
    with ProcessPoolExecutor(12) as ex:
        c = collections.Counter(k for ks in ex.map(keys, smp, chunksize=20) for k in set(ks))
    for k, v in c.most_common(60):
        print(f"{v:5d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
