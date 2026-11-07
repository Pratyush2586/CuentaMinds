"""Payment facts that live ONLY in the document PDFs (POLICIES §5.7-5.9), parsed once and cached.

The PDFs are generated text (no images / tables to OCR): pdfplumber text + multilingual regexes is enough.
Per AR invoice / AR credit note (keyed by entity + normalised document number):
  instalments         'Payment terms: 3 instalments at 60/90/120 days (6,919.30 / 6,919.30 / 6,919.31 USD)' (all amounts)
  payable             retention invoices: 'Amount payable at due date: Z' / 'Importe a pagar al vencimiento: Z'
  advance             'Less advance received against proforma PF..: X' (the invoice payment is total - X)
  replaces            'This invoice cancels and replaces invoice X' / 'anula y sustituye a la factura X'
  cancels             credit note 'CANCELLING OF INVOICE Nº X' (cancelled invoice + this note are never paid)
  paid_out            rebate credit note refunded by transfer (never netted) vs offset (netted)
"""
from __future__ import annotations

import csv
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from bankrec.text import norm

AMT = r"([\d][\d.,\s]*\d)"
_INST = re.compile(r"(\d+)\s*(?:instalments?|vencimientos|presta[cç][oõ]es|parcelas|[ée]ch[ée]ances|Raten|rate)\s*"
                   r"(?:at|a|à|de|zu|da)?\s*[\d/\s]+\s*(?:days|d[ií]as|jours|Tage|giorni|dias)\s*\(([^)]*)\)", re.I)
_AMTS = re.compile(r"\d[\d.,\s]*\d")
_PAYABLE = re.compile(r"(?:Amount payable at due date|Importe a pagar al vencimiento|Montant (?:à payer|payable)[^:]*|"
                      r"Valor a pagar[^:]*|Zahlbarer Betrag[^:]*|Importo da pagare[^:]*)\s*:\s*" + AMT, re.I)
_ADVANCE = re.compile(r"(?:Less advance received against proforma|A deducir anticipo[^:]*|D[ée]duction (?:de l')?acompte[^:]*|"
                      r"Abz[üu]glich Anzahlung[^:]*|Dedu[cç][aã]o (?:do )?adiantamento[^:]*)[^:]*:\s*" + AMT, re.I)
_REPLACES = re.compile(r"(?:cancels and replaces invoice|anula y sustituye a la factura|annule et remplace la facture|"
                       r"anula e substitui a fatura|annulla e sostituisce la fattura|storniert und ersetzt die Rechnung|"
                       r"ersetzt die Rechnung)\s+([A-Z0-9][A-Z0-9/\-]+)", re.I)
_CANCELS = re.compile(r"(?:CANCELLING OF INVOICE|ANULACI[ÓO]N DE LA FACTURA|ANNULATION DE LA FACTURE|STORNO DER RECHNUNG|"
                      r"ANULA[CÇ][AÃ]O DA FATURA|ANNULLAMENTO DELLA FATTURA)\s*N?[ºo°.]?\s*([A-Z0-9][A-Z0-9/\-]+)", re.I)
_PAID_OUT = re.compile(r"paid to your bank|refunded by bank transfer|abonar[áa] por transferencia|reembolsado mediante "
                       r"transferencia|rembours[ée]|vir[ée] sur votre compte|[üu]berwiesen|bonificato", re.I)
_OFFSET = re.compile(r"offset against|deducted from your next|se compensar[áa]|compens[ée]|verrechnet|compensad", re.I)


def amount(s: str) -> float | None:
    s = s.replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s) - s.rfind(",") == 3 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s) - s.rfind(".") == 4):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_text(txt: str) -> dict:
    t = " ".join(txt.split())
    out: dict = {}
    m = _INST.search(t)
    if m:   # '3 instalments at 60/90/120 days (6,919.30 / 6,919.30 / 6,919.31 USD)': every instalment amount
        vals = [amount(x) for x in _AMTS.findall(m.group(2).replace(" / ", "|").replace("/", "|")) if amount(x)]
        if vals:
            out["instalments"] = sorted(set(vals))
    for key, rx in (("payable", _PAYABLE), ("advance", _ADVANCE)):
        m = rx.search(t)
        if m:
            v = amount(m.group(m.lastindex))
            if v:
                out[key] = v
    for key, rx in (("replaces", _REPLACES), ("cancels", _CANCELS)):
        m = rx.search(t)
        if m:
            out[key] = norm(m.group(1).rstrip(".,"))
    if _PAID_OUT.search(t):
        out["paid_out"] = True
    elif _OFFSET.search(t):
        out["paid_out"] = False
    return out


def _one(args: tuple[str, dict]) -> dict | None:
    path, meta = args
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            txt = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return None
    facts = parse_text(txt)
    return {**meta, **facts} if facts else None


def load_pdf_facts(dirs: list[Path], cache_dir: Path, workers: int = 12) -> dict[tuple[str, str], dict]:
    """(entity, normalised document number) -> facts, for AR invoices and AR credit notes of the given phase dirs."""
    out: dict[tuple[str, str], dict] = {}
    for d in dirs:
        idx = d / "pdfs" / "index.csv"
        if not idx.exists():
            continue
        cache = cache_dir / f"pdffacts_{d.parent.name}_{d.name}.json"
        if cache.exists():
            facts = json.loads(cache.read_text(encoding="utf-8"))
        else:
            rows = [r for r in csv.DictReader(open(idx, encoding="utf-8"))
                    if r["document_type"] in ("invoice", "credit_note") and not os.path.basename(r["file"]).startswith("._")]
            jobs = [(str(d / "pdfs" / r["file"]), {"entity": r["entity"], "doc": norm(r["document_number"]),
                                                   "type": r["document_type"], "party": r["party_id"]}) for r in rows]
            with ProcessPoolExecutor(max_workers=workers) as ex:
                facts = [f for f in ex.map(_one, jobs, chunksize=50) if f]
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(facts), encoding="utf-8")
        for f in facts:
            out[(f["entity"], f["doc"])] = f
    return out
