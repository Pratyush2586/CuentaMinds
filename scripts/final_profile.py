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


def llm_triage(unseen: pd.DataFrame, base: pd.DataFrame, total: int) -> str:
    """Claude reads a sample of lines whose bank-text layout the parsers have never seen and says how to read them.
    Regexes fail silently on a new format; an LLM generalises. Advisory only: it proposes, the fix loop validates."""
    cols = ["entity", "bank", "statement_format", "amount", "currency", "text", "remittance_info", "ref2",
            "counterparty_name"]
    cols = [c for c in cols if c in unseen.columns]
    try:
        return _triage(unseen, base, total, cols)
    except Exception as e:  # advisory: never block the run
        return f"(Claude triage unavailable: {type(e).__name__})"


def _triage(unseen: pd.DataFrame, base: pd.DataFrame, total: int, cols: list[str]) -> str:
    samp = unseen.groupby("entity").head(8).head(40)[cols]
    known = base[base.entity.isin(samp.entity)].groupby("entity").head(3)[cols]
    user = f"""{len(unseen)} of {total} bank lines of the new data set have a bank-text layout never seen in the
baseline set the engine was built on. The engine finds the payer from the counterparty name / IBAN / text words and
the invoices from document numbers in text / remittance_info (regex tokens, e.g. FV24-0105515, INV25-0101966,
1072505511), and classifies non-invoice lines (bank fees, interest, taxes, payroll...) from text words.

Sample of UNSEEN-layout lines (CSV):
{samp.to_csv(index=False)}
Known-layout lines of the same entities, for contrast (CSV):
{known.to_csv(index=False)}
Answer in <= 25 lines: (1) what changed in the layout (new bank / language / field order / reference format);
(2) for each pattern: where the payer and the document numbers are, and a concrete regex or token rule to extract
them; (3) severity: does this likely break matching (HIGH / MEDIUM / LOW) and for roughly how many lines;
(4) the single most valuable parser fix. Do not invent fields not visible in the sample."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import analyst
        analyst.load_env()
        import anthropic
        r = anthropic.Anthropic(timeout=180).messages.create(
            model=analyst.MODEL, max_tokens=2000,
            system="Role: input-parser engineer for a bank-reconciliation engine. Be concrete and brief.",
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in r.content if b.type == "text")
    except Exception as e:  # advisory: never block the run
        return f"(Claude triage unavailable: {type(e).__name__})"


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
    unseen_lines = tp[~blob(tp).map(layout).isin(lay_b)]
    if len(unseen_lines) and "--no-llm" not in sys.argv:
        out += ["", "## Claude triage of unseen bank-text layouts (advisory)", llm_triage(unseen_lines, tb, len(tp))]
    text = "\n".join(out)
    print(text)
    f = ROOT / "submissions" / f"{phase}_profile.md"
    f.parent.mkdir(exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
