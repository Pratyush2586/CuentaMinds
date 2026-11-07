"""Mine the clause templates of the document PDFs (what information lives ONLY in the PDFs).

    python scripts/pdf_clauses.py [PER_TYPE=400] [PHASE_DIR=data/valtermia/train/train]

Extracts the text of a random sample of each document type (pdfplumber; the PDFs are generated text, no images or
tables), drops header/line-item/total lines, replaces digits with '#', and prints the most frequent remaining
sentences per type: payment clauses (instalments, retention, advance deduction, cancel-and-replace, credit-note
offset vs refund, bank-account changes...).
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

SKIP = re.compile(r"^(Documento sint|TOTAL|Base|IVA|TVA|Tax|Subtotal|MwSt|Total|Descri|Désign|Bezeich|Qty|Cant|Qtd|"
                  r"IBAN|VAT|CIF|NIF|Date|Data|Fecha|Datum|No\.|N[ºo°]|Rif|Ref|Bill|Client|Cliente|Kunde|Your PO|"
                  r"V/|Vencim|Due|Fällig|Échéance|Scadenza|Valtermia|valtermia|Forma de pago|Conditions|Condi|Terms|"
                  r"Zahlungsbed|Pagamento|Rechnung|Invoice|INVOICE|FACTURA|FATURA|FACTURE|AVOIR|CREDIT|ABONO|Gutschrift)", re.I)
ROOT = Path(__file__).resolve().parent.parent


def clauses(path: str) -> list[str]:
    try:
        with pdfplumber.open(path) as pdf:
            txt = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return []
    out = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if len(ln) < 25 or SKIP.match(ln):
            continue
        if re.search(r"\d+[.,]\d\d\s*$", ln) and len(re.findall(r"[A-Za-zÀ-ÿ]{4,}", ln)) < 4:
            continue                                   # line items: product, qty, price, amount
        out.append(re.sub(r"\d[\d.,/ -]*\d|\d", "#", ln))
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    per = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    d = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "data/valtermia/train/train") / "pdfs"
    rows = list(csv.DictReader(open(d / "index.csv", encoding="utf-8")))
    by = collections.defaultdict(list)
    for r in rows:
        by[r["document_type"]].append(str(d / r["file"]))
    random.seed(1)
    with ProcessPoolExecutor(12) as ex:
        for t, files in by.items():
            smp = random.sample(files, min(per, len(files)))
            c = collections.Counter(cl for cs in ex.map(clauses, smp, chunksize=10) for cl in set(cs))
            print(f"\n##### {t}  (sample {len(smp)} of {len(files)})", flush=True)
            for cl, n in c.most_common(16):
                if n >= 2:
                    print(f"  {n:4d}  {cl[:160]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
