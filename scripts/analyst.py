"""Claude analyst personas for the bankrec evaluation framework. ADVISORY ONLY: they read reports, never edit code,
never see any LIVE/FINAL answers (there are none), and their output is a markdown note for the human / coding agent.

    python scripts/analyst.py hypotheses                 # LIVE report + calibration + experiment log -> next experiments
    python scripts/analyst.py skeptic [COMMIT]           # overfitting review of one experiment's diff (default HEAD)
    python scripts/analyst.py final-review GATE_OUTPUT.txt   # go / no-go reasoning on the FINAL readiness gate output

Model: claude-opus-5-5 (adaptive thinking, effort high), server-side refusal fallback on. Reports are saved to
autoresearch/analyst/<timestamp>_<mode>.md with token usage and cost. Key: ANTHROPIC_API_KEY (env or git-ignored .env).
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "autoresearch" / "analyst"
MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.0, 20.0   # $ per 1M tokens (claude-opus-5-5)

CONTEXT = """You are part of the evaluation framework of team CuentaMinds in a bank-reconciliation hackathon.
The engine (Python, rules + one learned category table, no LLM in the scoring path) assigns every bank line of a
6-month phase to the open items it settles (or a no-item category) with an exception label, following POLICIES.md
exactly. Score = 100 x (0.5 pair-F1 + 0.3 line accuracy + 0.2 exception macro-F1).
Evaluation layers: (1) TRAIN months with real golden (dev months 2025-03, 2024-10; hold-out 2025-05) through an
autoresearch loop that keeps a change only if the mean rises >= 0.02 and no month drops > 0.30; (2) LIVE uploads
(platform score + per-segment accuracy, limited uploads); (3) FINAL: ONE scored upload, no feedback, data with
announced drift (new subsidiary ZA01/ZAR from 2026-01, renamed 2026-04, customers renamed / changing banks, slower
payments). A LIVE-only drift (Sept-2025 advice layout printing short invoice numbers) cost ~2.4 points until fixed.
Hard rules: no logic keyed to specific ids, customers, amounts or dates; thresholds must come from POLICIES.md or
from distributions observed in TRAIN golden; never tune on LIVE feedback without TRAIN evidence; fair play.
Be concrete and brief. Prefer generic, policy-grounded rules. Flag overfitting risk explicitly."""


def load_env() -> None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def run(*cmd: str) -> str:
    return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def ask(system_extra: str, user: str, mode: str) -> str:
    load_env()
    client = anthropic.Anthropic()
    policies = (ROOT / "data" / "valtermia" / "docs" / "POLICIES.md")
    system = [{"type": "text", "text": CONTEXT + "\n\nPOLICIES.md:\n" +
               (policies.read_text(encoding="utf-8") if policies.exists() else "(not available)"),
               "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": system_extra}]
    try:
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"}, output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"})
    except anthropic.BadRequestError as e:          # fallback beta unavailable: plain request
        print(f"(fallback not accepted: {e.message[:120]}; retrying without it)")
        resp = client.messages.create(model=MODEL, max_tokens=16000, system=system,
                                      messages=[{"role": "user", "content": user}],
                                      thinking={"type": "adaptive"}, output_config={"effort": "high"})
    except anthropic.RateLimitError:
        sys.exit("rate limited: try again in a minute")
    except anthropic.APIConnectionError:
        sys.exit("network error reaching the Claude API")
    if resp.stop_reason == "refusal":
        sys.exit("the model declined this request")
    text = "\n".join(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    cost = (u.input_tokens + (u.cache_creation_input_tokens or 0) * 1.25 + (u.cache_read_input_tokens or 0) * 0.1) \
        * PRICE_IN / 1e6 + u.output_tokens * PRICE_OUT / 1e6
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{datetime.now():%Y%m%d-%H%M%S}_{mode}.md"
    path.write_text(f"<!-- {MODEL} · in {u.input_tokens} out {u.output_tokens} · ~${cost:.3f} -->\n\n{text}\n", encoding="utf-8")
    print(text)
    print(f"\n[{MODEL} · in {u.input_tokens} (cache read {u.cache_read_input_tokens or 0}) out {u.output_tokens} · "
          f"~${cost:.3f} · saved {path.relative_to(ROOT)}]")
    return text


def tsv(path: Path, last: int = 40) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0] + "\n" + "\n".join(
        path.read_text(encoding="utf-8").splitlines()[1:][-last:]) if path.exists() else "(none)"


def cmd_hypotheses() -> None:
    report = run(sys.executable, "scripts/live_report.py")
    user = f"""LIVE per-segment report (newest upload vs previous vs TRAIN months, ranked by LIVE lines lost):
{report}

Calibration (TRAIN eval vs LIVE score):
{tsv(ROOT / 'bankrec_calibration.tsv')}

Autoresearch log (keep/discard of past experiments):
{tsv(ROOT / 'autoresearch' / 'results.tsv')}

Task: propose the 5 most valuable NEXT experiments. For each: segment(s) and expected lines/points at stake; the
likely mechanism (why it fails, especially if LIVE-only = drift); a generic, policy-grounded rule; how to verify it on
TRAIN dev months (and whether TRAIN can show it at all); overfitting risk (low/med/high) and why. Do not repeat
discarded ideas unless you explain what is different. End with one line: which experiment first."""
    ask("Role: senior reconciliation analyst.", user, "hypotheses")


def cmd_skeptic(commit: str) -> None:
    diff = run("git", "show", "--stat", "--patch", commit, "--", "src/bankrec")
    user = f"""Experiment diff ({commit}):
{diff[:60000]}

Autoresearch log:
{tsv(ROOT / 'autoresearch' / 'results.tsv', 15)}

Task: review ONLY for overfitting and robustness on unseen FINAL data. Check: literals keyed to ids / customers /
amounts / dates; constants not justified by POLICIES.md or TRAIN-wide distributions; rules that fire on very few lines
(memorising); behaviour that depends on LIVE-only observations; risk under the announced FINAL drift (new entity ZA01,
renames, bank changes, slower payments); runtime risk (FINAL ~220k lines must run in ~25 min). Verdict: KEEP / KEEP
WITH CHANGES / REVERT, with the 1-3 most important reasons."""
    ask("Role: skeptical reviewer whose only job is to prevent overfitting and FINAL-day breakage.", user, "skeptic")


def cmd_final_review(gate_file: str) -> None:
    gate = Path(gate_file).read_text(encoding="utf-8")
    user = f"""FINAL readiness gate output (label-free profile of the FINAL submission vs the best scored LIVE file):
{gate}

Calibration (TRAIN eval vs LIVE):
{tsv(ROOT / 'bankrec_calibration.tsv')}

Task: there is exactly ONE scored FINAL upload and ~1 hour left. For each WARN/FAIL decide: explainable drift
(acceptable) or likely breakage (must fix), and what to inspect first. Then a go / no-go recommendation with the
expected score range and the residual risks. If no-go, the single most likely fix."""
    ask("Role: release manager for a one-shot submission.", user, "final_review")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "hypotheses":
        cmd_hypotheses()
    elif mode == "skeptic":
        cmd_skeptic(sys.argv[2] if len(sys.argv) > 2 else "HEAD")
    elif mode == "final-review" and len(sys.argv) > 2:
        cmd_final_review(sys.argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
