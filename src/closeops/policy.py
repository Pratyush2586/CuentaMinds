"""Autonomy policy: load config and assign each finding an autonomy tier."""
from __future__ import annotations

from pathlib import Path

import yaml

from closeops.model import Finding, Tier


def load_policy(path: Path | str = "config/policy.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _tier(f: Finding, policy: dict) -> Tier:
    cfg = policy["tiers"]
    if f.check.startswith("iban_") or f.check.startswith("shared_iban"):
        return Tier.HOLD
    if f.category == "fraud" and f.severity == "high":
        return Tier.HOLD
    if (f.amount_eur or 0) >= policy["materiality_eur"]:
        return Tier.HOLD
    if f.confidence < cfg["hold_below_confidence"]:
        return Tier.HOLD
    if f.confidence >= cfg["auto_min_confidence"] and f.category in cfg["auto_categories"]:
        return Tier.AUTO
    return Tier.AUTO_NOTE


def assign_tiers(findings: list[Finding], policy: dict) -> list[Finding]:
    for f in findings:
        f.tier = _tier(f, policy)
        f.requires_human_review = f.tier is Tier.HOLD
    return findings
