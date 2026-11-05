"""Generic result exporter: findings, proposals, side tables, report and policy copy."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import yaml

from closeops.checks.fraud import SOFT_CHECKS
from closeops.model import Dataset, Finding

if TYPE_CHECKING:
    from closeops.pipeline import RunResult

FINDING_COLUMNS = ["finding_id", "check", "category", "severity", "entity", "record_type",
                   "primary_record_id", "record_ids", "confidence", "amount_eur", "tier",
                   "requires_human_review", "explanation", "resolved_by", "source_refs"]
MAX_REFS = 20
_ID_COLUMN = {"invoice": ("invoices", "invoice_id"),
              "bank_transaction": ("bank_transactions", "txn_id"),
              "gl_entry": ("gl_entries", "je_id"),
              "vendor": ("vendors", "vendor_id")}


def _json_default(o):
    """json.dump handler for numpy/pandas scalars and dates."""
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    if hasattr(o, "item"):  # numpy scalar
        return o.item()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def _ref(r) -> str:
    return f"{r.source_file}:{r.source_row}"


class _RefIndex:
    """One dict per table, built once, so resolving a finding is a lookup."""

    def __init__(self, ds: Dataset):
        self.by_id: dict[str, dict[str, list[str]]] = {}
        for rtype, (table, col) in _ID_COLUMN.items():
            idx: dict[str, list[str]] = defaultdict(list)
            if ds.has(table):
                for r in ds.table(table).itertuples(index=False):
                    idx[str(getattr(r, col))].append(_ref(r))
            self.by_id[rtype] = idx
        self.by_entity: dict[str, list[str]] = defaultdict(list)
        self.by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
        if ds.has("gl_entries"):
            for r in ds.table("gl_entries").itertuples(index=False):
                ent = str(r.entity)
                self.by_entity[ent].append(_ref(r))
                if pd.notna(r.counterparty_entity) and str(r.counterparty_entity).strip():
                    self.by_pair[(ent, str(r.counterparty_entity))].append(_ref(r))

    def refs(self, f: Finding) -> list[str]:
        ids = [str(i) for i in f.record_ids]
        out: list[str] = []
        if f.record_type in self.by_id:
            for i in ids:
                out += self.by_id[f.record_type].get(i, [])
        elif f.record_type == "entity":
            for i in ids:
                out += self.by_entity.get(i, [])
        elif f.record_type == "entity_pair" and len(ids) == 2:
            a, b = ids
            out = self.by_pair.get((a, b), []) + self.by_pair.get((b, a), [])
        seen: set[str] = set()
        out = [r for r in out if not (r in seen or seen.add(r))]
        if len(out) > MAX_REFS:
            return out[:MAX_REFS] + [f"+{len(out) - MAX_REFS} more"]
        return out


def _finding_row(f: Finding, refs: list[str]) -> dict:
    d = f.to_dict()
    return {"finding_id": d["finding_id"], "check": f.check, "category": f.category,
            "severity": f.severity, "entity": f.entity, "record_type": f.record_type,
            "primary_record_id": str(f.record_ids[0]) if f.record_ids else "",
            "record_ids": [str(i) for i in f.record_ids], "confidence": f.confidence,
            "amount_eur": f.amount_eur, "tier": d["tier"],
            "requires_human_review": f.requires_human_review, "explanation": f.explanation,
            "resolved_by": f.resolved_by, "source_refs": refs}


def _join(v):
    return ";".join(str(x) for x in v) if isinstance(v, (list, tuple, set)) else v


def _csv(df: pd.DataFrame, path: Path, columns: list[str] | None = None) -> None:
    if columns is not None:
        df = df.reindex(columns=columns) if not df.empty else pd.DataFrame(columns=columns)
    if not df.empty:
        df = df.apply(lambda col: col.map(_join))
    df.to_csv(path, index=False, encoding="utf-8")


def _entities(run: RunResult, findings: list[Finding]) -> list[str]:
    ents = {f.entity for f in findings if f.entity}
    for name in ("bank_transactions", "gl_entries", "invoices"):
        if run.dataset.has(name):
            ents |= {str(e) for e in run.dataset.table(name)["entity"].dropna()}
    return sorted(ents)


def _report(run: RunResult, findings: list[Finding]) -> str:
    ds = run.dataset
    lines = [f"# Close report - period ending {ds.period_end.isoformat()}", ""]
    txn_entity: dict[str, str] = {}
    if ds.has("bank_transactions"):
        bt = ds.table("bank_transactions")
        txn_entity = dict(zip(bt["txn_id"].astype(str), bt["entity"].astype(str)))
    lines_total: dict[str, int] = defaultdict(int)
    for e in txn_entity.values():
        lines_total[e] += 1
    matched: dict[str, int] = defaultdict(int)
    bm = run.side_tables.get("bank_matches")
    if bm is not None and not bm.empty:
        for t in bm["txn_id"].astype(str).unique():
            if t in txn_entity:
                matched[txn_entity[t]] += 1
    unbalanced = {f.entity for f in findings if f.check == "tb_unbalanced"}
    for ent in _entities(run, findings):
        mine = [f for f in findings if f.entity == ent]
        total = lines_total.get(ent, 0)
        pct = f"{100 * matched.get(ent, 0) / total:.1f}%" if total else "n/a"
        holds = sum(1 for f in mine if f.tier is not None and f.tier.value == "HOLD")
        lines += [f"## Entity {ent}", "", "| Category | Findings |", "| --- | --- |"]
        by_cat: dict[str, int] = defaultdict(int)
        for f in mine:
            by_cat[f.category] += 1
        lines += [f"| {c} | {n} |" for c, n in sorted(by_cat.items())] or ["| (none) | 0 |"]
        lines += ["", f"- Bank lines reconciled: {pct} ({matched.get(ent, 0)} of {total})",
                  f"- Trial balance balanced: {'no' if ent in unbalanced else 'yes'}",
                  f"- HOLD findings: {holds}", ""]
    lines += ["## Run notes", ""]
    lines += [f"- {n}" for n in run.notes] or ["- none"]
    return "\n".join(lines) + "\n"


def write_results(run: RunResult, policy: dict) -> list[Path]:
    out = Path(run.run_dir)
    out.mkdir(parents=True, exist_ok=True)
    soft = bool((policy.get("fraud") or {}).get("export_soft_signals", False))
    findings = [f for f in run.findings if soft or f.check not in SOFT_CHECKS]
    index = _RefIndex(run.dataset)
    rows = [_finding_row(f, index.refs(f)) for f in findings]
    paths: list[Path] = []

    def p(name: str) -> Path:
        paths.append(out / name)
        return out / name

    _csv(pd.DataFrame(rows), p("findings.csv"), FINDING_COLUMNS)
    p("findings.json").write_text(
        json.dumps(rows, indent=2, default=_json_default), encoding="utf-8")
    _csv(run.journal_entries, p("journal_entries.csv"))
    _csv(run.payments, p("payment_proposals.csv"))
    bm = run.side_tables.get("bank_matches", pd.DataFrame())
    _csv(bm, p("bank_reconciliation.csv"),
         list(bm.columns) if len(bm.columns) else ["txn_id", "matched_ids", "method", "confidence"])
    for name in ("match_status", "trial_balance"):
        _csv(run.side_tables.get(name, pd.DataFrame()), p(f"{name}.csv"))
    p("close_report.md").write_text(_report(run, findings), encoding="utf-8")
    p("policy_used.yaml").write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return paths
