"""Text normalisation and the document-number index (bank texts quote our numbers noisily)."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9\-/.]*[A-Z0-9]|[A-Z0-9]")
LEGAL = {"SL", "SLU", "SA", "SAU", "SCOOP", "COOP", "GMBH", "CO", "KG", "EK", "AG", "SARL", "SAS", "EURL", "SNC",
         "LTD", "LIMITED", "PLC", "LLC", "INC", "CORP", "SRL", "SPA", "BV", "NV", "OU", "SPZOO", "ZOO", "SP", "LDA",
         "SARLAU", "SOC", "Y", "AND", "DE", "LA", "EL", "THE", "&", "Y", "ET", "UND", "DA", "DO", "DEL", "LOS", "LAS"}


def fold(s: str) -> str:
    """Upper-case ASCII fold: 'Bien-Être Gutiérrez' -> 'BIEN-ETRE GUTIERREZ'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def norm(s: str) -> str:
    return _NON_ALNUM.sub("", fold(s))


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def doc_keys(doc: str) -> set[str]:
    """Lookup keys of a printed document number: full, without prefix letters, without leading zeros."""
    n = norm(doc)
    if not n:
        return set()
    keys = {n}
    d = digits(n)
    if len(d) >= 5:
        keys.add(d)
        keys.add(d.lstrip("0") or "0")
    return keys


def bank_blob(row) -> str:
    """All free text of a bank line, with N43 38-char record joins (' | ') also glued back together."""
    parts = [row.get("text", ""), row.get("ref1", ""), row.get("ref2", ""), row.get("remittance_info", ""),
             row.get("end_to_end_id", "")]
    t = fold(" ".join(p for p in parts if p))
    return t + " " + t.replace(" | ", "")


def tokens(blob: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(blob):
        n = norm(tok)
        if len(n) >= 4:
            out.append(n)
        for piece in re.split(r"[\-/.]", tok):  # 'FV24-0511465' also as '0511465'
            p = norm(piece)
            if len(p) >= 5 and p != n:
                out.append(p)
    return out


def name_key(s: str) -> str:
    """Party name without legal forms/punctuation: 'Aguas Calvo Plus SLU' -> 'AGUAS CALVO PLUS'."""
    words = re.split(r"[^A-Z0-9]+", fold(s).replace(".", ""))
    return " ".join(w for w in words if w and w not in LEGAL)


class DocIndex:
    """key -> item row indexes. Keys from doc_keys(); lookups also try digits-only of the token."""

    def __init__(self, docs: list[str]):
        self.map: dict[str, list[int]] = defaultdict(list)
        for i, d in enumerate(docs):
            for k in doc_keys(d):
                self.map[k].append(i)

    def lookup(self, token: str) -> list[int]:
        hits = self.map.get(token)
        if hits:
            return hits
        d = digits(token)
        if len(d) >= 6 and d != token:
            return self.map.get(d) or self.map.get(d.lstrip("0"), [])
        return []
