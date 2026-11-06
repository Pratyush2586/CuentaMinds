"""End-to-end close: ingest -> checks -> tiers -> evidence log -> findings export."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from closeops.checks.registry import run_checks
from closeops.evidence import EvidenceLog
from closeops.export.generic import write_results
from closeops.ingest.mapping import load_dataset
from closeops.journal import JE_COLUMNS, PAY_COLUMNS, propose_journal_entries, propose_payments
from closeops.model import Dataset, Finding
from closeops.policy import assign_tiers, load_policy

logger = logging.getLogger("closeops.pipeline")


@dataclass
class RunResult:
    dataset: Dataset
    findings: list[Finding]
    side_tables: dict[str, pd.DataFrame]
    notes: list[str]
    journal_entries: pd.DataFrame = field(default_factory=pd.DataFrame)
    payments: pd.DataFrame = field(default_factory=pd.DataFrame)
    run_dir: Path = Path(".")
    close_ready: bool | None = None  # None: no Controller sign-off was attempted


def _new_run_dir(out_dir: Path) -> Path:
    base = Path(out_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir, n = base, 1
    while run_dir.exists():
        n += 1
        run_dir = base.with_name(f"{base.name}-{n}")
    run_dir.mkdir(parents=True)
    return run_dir


def run_close(raw_dir: Path, mapping: Path, policy_path: Path, out_dir: Path,
              use_agents: bool = False) -> RunResult:
    run_dir = _new_run_dir(Path(out_dir))
    log = EvidenceLog(run_dir / "evidence.jsonl")
    policy = load_policy(policy_path)

    def stage(action: str, output: dict) -> None:
        log.append("pipeline", action, [], {}, output, None, None)

    ds = load_dataset(Path(raw_dir), Path(mapping))
    stage("ingest", {"tables": {n: int(len(df)) for n, df in sorted(ds.tables.items())},
                     "period_end": ds.period_end.isoformat()})

    findings, side_tables, check_notes = run_checks(ds, policy)
    notes = list(ds.notes) + check_notes
    stage("checks", {"findings": len(findings), "notes": notes})

    assign_tiers(findings, policy)
    tiers: dict[str, int] = {}
    for f in findings:
        tiers[f.tier.value] = tiers.get(f.tier.value, 0) + 1
    stage("tiers", {"by_tier": tiers})

    llm = None
    if use_agents:
        from closeops.agents import team  # lazy: rules-only runs never import the SDK
        try:
            llm = team.make_llm(ds, policy)
        except Exception as exc:
            notes.append(f"agents unavailable: {exc}")
        if llm is not None:
            findings = team.resolve_residuals(ds, findings, policy, llm, log, notes)
            if any(n.startswith("agents unavailable") for n in notes):
                llm = None  # circuit breaker tripped: rules-only, no Controller
            stage("agents", {"findings": len(findings), "notes": notes})

    def guarded(name: str, fn, default, *args):
        """Post-check stages: a failure becomes a note and the findings still export."""
        try:
            return fn(*args)
        except Exception as exc:
            logger.exception("stage %s failed", name)
            notes.append(f"error in {name}: {type(exc).__name__}: {exc}")
            return default

    jes = guarded("propose_journal_entries", propose_journal_entries,
                  pd.DataFrame(columns=JE_COLUMNS), ds, findings, policy)
    payments = guarded("propose_payments", propose_payments, pd.DataFrame(columns=PAY_COLUMNS),
                       ds, findings, policy, side_tables.get("bank_matches"))

    def log_proposals() -> None:
        for je_id, g in jes.groupby("je_id", sort=True):
            lines = [{"account": r.account, "debit": r.debit, "credit": r.credit}
                     for r in g.itertuples()]
            log.append("rule", "propose_je", [g["finding_id"].iloc[0]], {},
                       {"je_id": je_id, "lines": lines}, None, g["tier"].iloc[0])
        for r in payments.itertuples():
            log.append("rule", "propose_payment", [r.invoice_id], {},
                       {"invoice_id": r.invoice_id, "tier": r.tier, "block_reason": r.block_reason},
                       None, r.tier)

    guarded("log_proposals", log_proposals, None)

    close_ready = None
    if llm is not None:
        memo, close_ready = team.controller_memo(ds, findings, jes, llm)
        (run_dir / "close_memo.md").write_text(memo.rstrip() + "\n", encoding="utf-8")
        log.append("Controller", "controller_memo", [], {},
                   {"close_ready": close_ready, "memo": memo}, None, None)

    def log_flags() -> None:
        for f in findings:
            log.append("rule", "flag", list(f.record_ids), f.evidence,
                       {"finding_id": f.finding_id, "check": f.check, "category": f.category,
                        "severity": f.severity}, f.confidence, f.tier.value)

    guarded("log_flags", log_flags, None)

    result = RunResult(dataset=ds, findings=findings, side_tables=side_tables, notes=notes,
                       journal_entries=jes, payments=payments, run_dir=run_dir,
                       close_ready=close_ready)
    written = write_results(result, policy)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in written}
    stage("export", {"files": hashes, "findings": len(findings),
                     "notes": notes})
    return result
