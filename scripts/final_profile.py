"""Input-parser agent (label-free): profile a phase's RAW inputs against a baseline phase BEFORE the engine runs.

    python scripts/final_profile.py final            # FINAL vs LIVE (the scored baseline)
    python scripts/final_profile.py live --base train

Finds input drift that would silently cost matches: new entities / statement formats / banks / currencies, bank-text
layouts never seen before, invoice numbering shapes the reference resolver has not seen, lines that quote no document
number, missing side files (advices, batches, PDFs). Each problem is a '[WARN] ...' line; the report is written to
submissions/<phase>_profile.md and fed to the release-manager review (scripts/final_run.py). Never uses labels.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from bankrec import data as D  # noqa: E402

DOCNUM = re.compile(r"\b[A-Z]{0,5}\d{2}[-/]?\d{4,}\b|\b\d{7,}\b")


def shape(s: str) -> str:
    return re.sub(r"\d+", "9", re.sub(r"[A-Z]+", "A", str(s).upper()))


def layout(t: str) -> str:
    w = re.findall(r"[A-Z]{3,}", str(t).upper())
    return " ".join(w[:2])


def load(phase: str) -> dict:
    d = D.find_phase_dir(ROOT / "data" / "valtermia", phase)
    tx = D.read(d / "bank_transactions.csv.gz")
    docs = []
    for f in ("ar_invoices.csv.gz", "ap_invoices.csv.gz", "credit_notes.csv.gz"):
        if (d / f).exists():
            x = D.read(d / f)
            col = next((c for c in x.columns if c in ("invoice_number", "document_number", "credit_note_number", "doc")), None)
            if col:
                docs.append(x[[col, "entity"]].rename(columns={col: "doc"}) if "entity" in x else x[[col]].rename(columns={col: "doc"}))
    docs = pd.concat(docs) if docs else pd.DataFrame(columns=["doc", "entity"])
    side = {p.name: sum(1 for _ in p.rglob("*") if _.is_file()) for p in d.iterdir() if p.is_dir()}
    return {"dir": d, "tx": tx, "docs": docs, "side": side}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    phase = sys.argv[1] if len(sys.argv) > 1 else "final"
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "live"
    P, B = load(phase), load(base)
    tp, tb = P["tx"], B["tx"]
    out, warn = [f"# Input profile: {phase} vs {base}", ""], []
    out.append(f"lines {len(tp)} (base {len(tb)}) · dates {tp.booking_date.min()} .. {tp.booking_date.max()}")
    for col in ("entity", "statement_format", "bank", "currency"):
        new = sorted(set(tp[col].dropna()) - set(tb[col].dropna()))
        if new:
            n = int(tp[col].isin(new).sum())
            warn.append(f"[WARN] new {col}: {new[:10]} ({n} lines, {n / len(tp):.1%})")
    blob = lambda t: (t.text.fillna("") + " " + t.remittance_info.fillna("")).str.upper()  # noqa: E731
    qp, qb = blob(tp).str.contains(DOCNUM), blob(tb).str.contains(DOCNUM)
    out += ["", "| entity | lines | quotes a doc no. | base | unseen text layout |", "|---|---|---|---|---|"]
    lay_b = set(blob(tb).map(layout))
    for e, g in tp.groupby("entity"):
        gb = tb[tb.entity == e]
        q, qb_e = qp[g.index].mean(), (qb[gb.index].mean() if len(gb) else float("nan"))
        unseen = (~blob(g).map(layout).isin(lay_b)).mean()
        out.append(f"| {e} | {len(g)} | {q:.1%} | {qb_e:.1%} | {unseen:.1%} |")
        if len(gb) and q < qb_e - 0.10:
            warn.append(f"[WARN] {e}: lines quoting a document number fell {qb_e:.1%} -> {q:.1%} (reference format changed?)")
        if unseen > 0.15 and len(g) >= 200:
            warn.append(f"[WARN] {e}: {unseen:.1%} of lines have a bank-text layout never seen in {base} (new bank / format?)")
    sp, sb = set(P["docs"].doc.map(shape)), B["docs"].doc.map(shape).value_counts()
    newshape = P["docs"].doc.map(shape).isin(set(sb.index))
    out += ["", f"document numbers: {len(P['docs'])} · shape known in {base}: {newshape.mean():.1%}"]
    if len(P["docs"]) and newshape.mean() < 0.9:
        ex = P["docs"].doc[~newshape].head(5).tolist()
        warn.append(f"[WARN] {1 - newshape.mean():.1%} of document numbers have a new shape, e.g. {ex} (reference resolver)")
    out += ["", "side files: " + ", ".join(f"{k} {v} (base {B['side'].get(k, 0)})" for k, v in sorted(P["side"].items()))]
    for k, v in B["side"].items():
        if v and not P["side"].get(k):
            warn.append(f"[WARN] side folder '{k}' missing or empty (base has {v} files)")
    out += ["", "## Flags"] + (warn or ["[OK] no input drift flagged"])
    text = "\n".join(out)
    print(text)
    f = ROOT / "submissions" / f"{phase}_profile.md"
    f.parent.mkdir(exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
