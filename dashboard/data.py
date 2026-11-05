"""Pure helpers for the dashboard: read exported run files, resolve source rows, log decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from closeops.evidence import EvidenceLog
from closeops.ingest.profile import read_raw

TABLES = {"journal": "journal_entries.csv", "payments": "payment_proposals.csv",
          "bank_rec": "bank_reconciliation.csv", "match_status": "match_status.csv",
          "trial_balance": "trial_balance.csv"}
DECISION_TIER = {"approve": "APPROVED", "reject": "REJECTED"}
LOGGED_TIER = {"approve": "HUMAN_APPROVED", "reject": "HUMAN_REJECTED"}  # evidence-log tier


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def latest_run(results_dir: Path) -> Path | None:
    """Newest run subdirectory (run dirs are timestamp-named)."""
    root = Path(results_dir)
    if not root.is_dir():
        return None
    runs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "findings.json").exists())
    return runs[-1] if runs else None


def resolve_run_dir(session_run_dir: str | None, results_dir: Path,
                    previous_results_dir: str | Path | None) -> Path | None:
    """Run to show: the session's run unless the Results dir changed or the run has no
    findings.json; otherwise the newest run under results_dir (None if there is none)."""
    if previous_results_dir is not None and Path(previous_results_dir) != Path(results_dir):
        session_run_dir = None
    if session_run_dir and (Path(session_run_dir) / "findings.json").exists():
        return Path(session_run_dir)
    return latest_run(results_dir)


def load_decisions(run_dir: Path) -> dict[str, str]:
    p = Path(run_dir) / "human_decisions.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _shown_tier(tier: str, key: str, decisions: dict[str, str]) -> str:
    return DECISION_TIER.get(decisions.get(key, ""), tier)


def load_run(run_dir: Path) -> dict:
    """Everything the dashboard shows, read from the exported files only."""
    run_dir = Path(run_dir)
    fp = run_dir / "findings.json"
    rows = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
    findings = pd.DataFrame(rows)
    decisions = load_decisions(run_dir)
    if not findings.empty:
        findings["display_tier"] = [_shown_tier(t, i, decisions)
                                    for t, i in zip(findings["tier"], findings["finding_id"])]
    out = {name: _read_csv(run_dir / fn) for name, fn in TABLES.items()}
    pay = out["payments"]
    if not pay.empty:
        pay["display_tier"] = [_shown_tier(t, i, decisions)
                               for t, i in zip(pay["tier"], pay["invoice_id"])]
    ev = run_dir / "evidence.jsonl"
    events = [json.loads(ln) for ln in ev.read_text(encoding="utf-8").splitlines()
              if ln.strip()] if ev.exists() else []
    report = run_dir / "close_report.md"
    out.update(findings=findings, decisions=decisions, events=events,
               report=report.read_text(encoding="utf-8") if report.exists() else "",
               evidence_ok=EvidenceLog.verify(ev), evidence_status=EvidenceLog.status(ev))
    return out


def reconciled_pct(run: dict) -> float | None:
    """matched bank txns / (matched + bank_unmatched findings)."""
    bm, f = run["bank_rec"], run["findings"]
    matched = int(bm["txn_id"].nunique()) if not bm.empty and "txn_id" in bm else 0
    unmatched = int((f["check"] == "bank_unmatched").sum()) if not f.empty else 0
    total = matched + unmatched
    return 100 * matched / total if total else None


def source_rows(raw_dir: Path, refs: list[str]) -> pd.DataFrame:
    """Raw rows for 'file:row' refs ('+N more' and unreadable refs are skipped)."""
    wanted: dict[str, list[int]] = {}
    for ref in refs:
        name, _, row = str(ref).rpartition(":")
        if name and row.isdigit():
            wanted.setdefault(name, []).append(int(row))
    frames = []
    for name, nums in wanted.items():
        path = Path(raw_dir) / name
        if not path.is_file():
            continue
        try:
            raw = read_raw(path)
        except Exception:  # unreadable raw file: show what we can
            continue
        offset = 1 if path.suffix.lower() == ".json" else 2
        df = raw.copy()
        df.insert(0, "source_row", df.index + offset)
        df = df[df["source_row"].isin(nums)]
        df.insert(0, "source_file", name)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def record_decision(run_dir: Path, item_id: str, decision: str) -> str:
    """Log a human approve/reject to the evidence chain and decisions file; return shown tier."""
    if decision not in DECISION_TIER:
        raise ValueError(f"decision must be approve or reject, got {decision!r}")
    run_dir = Path(run_dir)
    run = load_run(run_dir)
    previous = None
    for df, col, tcol in ((run["findings"], "finding_id", "tier"),
                          (run["payments"], "invoice_id", "tier")):
        if not df.empty and item_id in set(df[col]):
            previous = str(df.loc[df[col] == item_id, tcol].iloc[0])
            break
    EvidenceLog(run_dir / "evidence.jsonl").append(
        "human", decision, [item_id], {},
        {"decision": decision, "previous_tier": previous}, None, LOGGED_TIER[decision])
    decisions = load_decisions(run_dir)
    decisions[item_id] = decision
    (run_dir / "human_decisions.json").write_text(
        json.dumps(decisions, indent=2), encoding="utf-8")
    return DECISION_TIER[decision]
