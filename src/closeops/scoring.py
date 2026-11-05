"""Local scorer: compare findings against the synthetic answer key."""
from __future__ import annotations

from collections import defaultdict

from closeops.checks.fraud import SOFT_CHECKS
from closeops.model import Finding

_JOINED_TYPES = {"entity_pair", "vendor"}


def _get(f: Finding | dict, key: str):
    return f[key] if isinstance(f, dict) else getattr(f, key)


def _primary_id(f: Finding | dict) -> str:
    ids = [str(i) for i in _get(f, "record_ids")]
    if _get(f, "record_type") in _JOINED_TYPES:
        return "|".join(sorted(ids))
    return ids[0] if ids else ""


def _metrics(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def score(findings: list[Finding | dict], answer_key: list[dict]) -> dict:
    """A key entry is hit when a finding has the same category and primary record id.

    tp is counted once per hit key entry (under the first hitting finding's check), fp per
    non-matching or repeat-hit finding (under its check), fn per unhit key entry (under the key's check).
    """
    hard = [f for f in findings if _get(f, "check") not in SOFT_CHECKS]
    keys = {(k["category"], str(k["record_id"])): k for k in answer_key}
    hit: set[tuple[str, str]] = set()
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    by_chk: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    total = [0, 0, 0]

    def bump(category: str, check: str, idx: int) -> None:
        by_cat[category][idx] += 1
        by_chk[check][idx] += 1
        total[idx] += 1

    for f in hard:
        ident = (_get(f, "category"), _primary_id(f))
        if ident in keys and ident not in hit:
            hit.add(ident)
            bump(ident[0], _get(f, "check"), 0)
        else:  # no key entry, or the key entry was already hit by an earlier finding
            bump(ident[0], _get(f, "check"), 1)
    for ident, k in keys.items():
        if ident not in hit:
            bump(k["category"], k["check"], 2)
    return {
        "overall": _metrics(*total),
        "by_category": {c: _metrics(*v) for c, v in sorted(by_cat.items())},
        "by_check": {c: _metrics(*v) for c, v in sorted(by_chk.items())},
    }
