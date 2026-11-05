"""Agent team: Claude reviews only the residual findings the rules cannot settle.

Any agent failure leaves the finding exactly as the rules produced it.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

import pandas as pd

from closeops.agents.tools import TOOL_SPECS, dispatch
from closeops.checks.fraud import SOFT_CHECKS
from closeops.evidence import EvidenceLog
from closeops.model import Dataset, Finding, Tier
from closeops.policy import assign_tiers

TRIAGE_MODEL = "claude-sonnet-5-5"
CONTROLLER_MODEL = "claude-opus-5-5"
ROLES = {"duplicate": "AP Clerk", "match": "AP Clerk", "bankrec": "Reconciler",
         "tax": "Tax Reviewer", "fraud": "Fraud Analyst"}
VERDICTS = ("confirm", "dismiss", "escalate")
MAX_TURNS = 6
MAX_EXPLANATION = 600
MAX_MEMO = 4000  # the Controller's explanation is the close memo
MAX_WORKERS = 8
DEFAULT_MAX_RESIDUALS = 40
DEFAULT_WALL_CLOCK_SECONDS = 300
_clock = time.monotonic  # patched in tests


class LLM(Protocol):
    def resolve(self, role: str, model: str, finding: dict, context: dict) -> dict:
        """Return {"verdict": "confirm|dismiss|escalate", "confidence": float, "explanation": str}."""
        ...


class AgentError(RuntimeError):
    pass


class AgentsUnavailable(AgentError):
    """Credentials missing/rejected: no agent call can succeed, so stop trying."""


SUBMIT_VERDICT = {
    "name": "submit_verdict", "strict": True,
    "description": "Submit your final verdict. Call exactly once, after gathering evidence.",
    "input_schema": {
        "type": "object",
        "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)},
                       "confidence": {"type": "number"},
                       "explanation": {"type": "string"}},
        "required": ["verdict", "confidence", "explanation"], "additionalProperties": False},
}

_SYSTEM = (
    "You are the {role} on an autonomous month-end finance close team. Deterministic rules "
    "have flagged the item you are given; you review it because the rules were not certain. "
    "Use the evidence tools to look at the underlying records. Never compute or invent "
    "amounts; rely on tool output. When done, call submit_verdict exactly once with: "
    "verdict 'confirm' (the finding is a real issue), 'dismiss' (a false positive), or "
    "'escalate' (a human must decide); confidence between 0 and 1; and an explanation of at "
    "most 600 characters citing record ids. Prefer 'escalate' when evidence is thin or "
    "payment details changed."
)
_CONTROLLER_SYSTEM = (
    "You are the Controller signing off a month-end close. You receive a summary of all "
    "findings and proposed journal entries. Verdict 'confirm' means the close is ready to be "
    "signed off; 'escalate' or 'dismiss' means it is not. Put a concise close memo in "
    "markdown (at most 4000 characters) in the explanation. Call submit_verdict exactly once."
)


def _parse_verdict(inp: dict, limit: int = MAX_EXPLANATION) -> dict:
    verdict = inp.get("verdict")
    if verdict not in VERDICTS:
        raise AgentError(f"invalid verdict {verdict!r}")
    try:
        conf = min(1.0, max(0.0, float(inp.get("confidence"))))
    except (TypeError, ValueError):
        raise AgentError("invalid confidence") from None
    return {"verdict": verdict, "confidence": conf,
            "explanation": str(inp.get("explanation", ""))[:limit]}


class ClaudeLLM:
    """Messages API implementation with a manual tool loop (see agent-api-notes.md)."""

    def __init__(self, ds: Dataset, client=None):
        import anthropic
        self.ds = ds
        self.client = client if client is not None else anthropic.Anthropic(timeout=60.0, max_retries=2)

    def resolve(self, role: str, model: str, finding: dict, context: dict) -> dict:
        import anthropic
        try:
            return self._loop(role, model, finding, context)
        except AgentError:
            raise
        except TypeError as exc:  # SDK: no api_key / auth_token / credentials resolved
            if "authentication" in str(exc):
                raise AgentsUnavailable(str(exc)) from exc
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                anthropic.BadRequestError, anthropic.NotFoundError) as exc:
            # credentials, model name or request shape: every other call fails the same way
            raise AgentsUnavailable(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise AgentError("RateLimitError") from exc
        except anthropic.APIStatusError as exc:
            raise AgentError(f"APIStatusError {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AgentError("APIConnectionError") from exc

    def _loop(self, role: str, model: str, finding: dict, context: dict) -> dict:
        system = _CONTROLLER_SYSTEM if role == "Controller" else _SYSTEM.format(role=role)
        prompt = json.dumps({"item": finding, "context": context}, default=str, sort_keys=True)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tools = TOOL_SPECS + [SUBMIT_VERDICT]
        effort = "medium" if model == CONTROLLER_MODEL else "low"
        for _ in range(MAX_TURNS):
            response = self.client.beta.messages.create(
                model=model, max_tokens=4000, betas=["server-side-fallback-2026-07-01"],
                fallbacks="default", output_config={"effort": effort}, system=system,
                tools=tools, tool_choice={"type": "auto"}, messages=messages)
            if response.stop_reason == "refusal":
                raise AgentError("refusal")
            uses = [b for b in response.content if b.type == "tool_use"]
            for b in uses:
                if b.name == "submit_verdict":
                    return _parse_verdict(b.input, MAX_MEMO if role == "Controller" else MAX_EXPLANATION)
            if response.stop_reason != "tool_use" or not uses:
                raise AgentError("no verdict submitted")
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for b in uses:
                out = dispatch(self.ds, b.name, b.input)
                block = {"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)}
                if "error" in out:
                    block["is_error"] = True
                results.append(block)
            messages.append({"role": "user", "content": results})
        raise AgentError(f"no verdict within {MAX_TURNS} turns")


def _is_iban_rule(check: str) -> bool:
    return check.startswith("iban_") or check == "shared_iban"


def _selected(f: Finding, policy: dict) -> bool:
    if f.check in SOFT_CHECKS or f.category not in ROLES:
        return False
    cfg = policy["tiers"]
    return (f.category == "fraud"
            or cfg["hold_below_confidence"] <= f.confidence < cfg["auto_min_confidence"])


_OVER_BUDGET = "skipped: wall clock"


def _call(llm: LLM, role: str, f: Finding, stop: threading.Event, deadline: float):
    if stop.is_set():
        return None, "skipped: agents unavailable"
    if _clock() >= deadline:  # budget spent: dispatch nothing new, keep the rules result
        return None, _OVER_BUDGET
    try:
        ctx = {"finding_id": f.finding_id, "entity": f.entity}
        return _parse_verdict(llm.resolve(role, TRIAGE_MODEL, f.to_dict(), ctx)), None
    except AgentsUnavailable as exc:
        stop.set()
        return None, str(exc)
    except Exception as exc:  # any agent failure -> rules-only result
        return None, str(exc) or type(exc).__name__


def resolve_residuals(ds: Dataset, findings: list[Finding], policy: dict, llm: LLM,
                      log: EvidenceLog, notes: list[str] | None = None) -> list[Finding]:
    notes = notes if notes is not None else []
    cfg = policy.get("agents") or {}
    limit = int(cfg.get("max_residuals", DEFAULT_MAX_RESIDUALS))
    budget = float(cfg.get("wall_clock_seconds", DEFAULT_WALL_CLOCK_SECONDS))
    residuals = sorted((f for f in findings if _selected(f, policy)),
                       key=lambda f: (-(f.amount_eur or 0), f.finding_id))
    if len(residuals) > limit:
        notes.append(f"agents: {limit} of {len(residuals)} residuals reviewed "
                     f"(max_residuals={limit}); {len(residuals) - limit} left to the rules")
    picked = sorted(residuals[:limit], key=lambda f: f.finding_id)
    stop = threading.Event()  # circuit breaker: set on the first authentication failure
    unavailable: list[str] = []
    deadline = _clock() + budget

    def call(f: Finding):
        out = _call(llm, ROLES[f.category], f, stop, deadline)
        if out[0] is None and stop.is_set() and not out[1].startswith("skipped"):
            unavailable.append(out[1])
        return out

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        outcomes = list(pool.map(call, picked))
    if stop.is_set():  # leave every finding exactly as the rules produced it
        notes.append(f"agents unavailable: {sorted(unavailable)[0]}")
        return findings

    over_budget = sum(err == _OVER_BUDGET for _, err in outcomes)
    if over_budget:
        notes.append(f"agents: wall_clock_seconds={budget:g} reached; "
                     f"{over_budget} residuals left to the rules")

    dismissed: set[int] = set()
    escalated: list[Finding] = []
    applied: list[tuple[Finding, str, dict | None, str | None]] = []
    for f, (verdict, err) in zip(picked, outcomes):
        role = ROLES[f.category]
        if err == _OVER_BUDGET:
            continue
        if verdict is None:
            notes.append(f"agent error: {role} on {f.finding_id}: {err}")
            applied.append((f, role, None, err))
            continue
        effective = verdict["verdict"]
        if effective == "dismiss" and _is_iban_rule(f.check):
            effective = "escalate"
            notes.append(f"agent dismiss overridden to escalate for {f.finding_id} (IBAN rule)")
        if effective == "dismiss":
            dismissed.add(id(f))
        else:
            if effective == "confirm":  # escalate / overridden dismiss keep the rule confidence
                f.confidence = max(f.confidence, verdict["confidence"])
            f.explanation = verdict["explanation"]
            f.resolved_by = role
            if effective == "escalate":
                escalated.append(f)
        applied.append((f, role, {**verdict, "applied": effective}, None))

    kept = [f for f in findings if id(f) not in dismissed]
    assign_tiers(kept, policy)
    for f in escalated:  # escalation survives re-tiering
        f.tier = Tier.HOLD
        f.requires_human_review = True

    for f, role, out, err in applied:
        if out is None:
            log.append(role, "agent_error", [f.finding_id], {}, {"error": err}, None,
                       f.tier.value if f.tier else None)
        else:
            tier = None if id(f) in dismissed else f.tier.value
            log.append(role, "agent_review", [f.finding_id], {}, out, out["confidence"], tier)
    return kept


def controller_summary(findings: list[Finding], journal_entries: pd.DataFrame) -> dict:
    by_tier: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for f in findings:
        t = f.tier.value if f.tier else "UNASSIGNED"
        by_tier[t] = by_tier.get(t, 0) + 1
        by_category[f.category] = by_category.get(f.category, 0) + 1
    held = sorted((f for f in findings if f.tier is Tier.HOLD),
                  key=lambda f: -(f.amount_eur or 0))[:25]
    return {
        "findings_total": len(findings), "by_tier": dict(sorted(by_tier.items())),
        "by_category": dict(sorted(by_category.items())),
        "held": [{"finding_id": f.finding_id, "check": f.check, "severity": f.severity,
                  "amount_eur": f.amount_eur, "confidence": f.confidence,
                  "resolved_by": f.resolved_by} for f in held],
        "journal_entries": (int(journal_entries["je_id"].nunique())
                            if journal_entries is not None and "je_id" in journal_entries else 0),
    }


def controller_memo(ds: Dataset, findings: list[Finding], journal_entries: pd.DataFrame,
                    llm: LLM) -> tuple[str, bool]:
    try:
        summary = controller_summary(findings, journal_entries)
        v = _parse_verdict(llm.resolve("Controller", CONTROLLER_MODEL, summary,
                                       {"period_end": ds.period_end.isoformat()}), MAX_MEMO)
    except Exception as exc:
        return f"Controller unavailable: {str(exc) or type(exc).__name__}; close not signed off.", False
    return v["explanation"], v["verdict"] == "confirm"
