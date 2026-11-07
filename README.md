# CuentaMinds: bank reconciliation engine for Grupo Valtermia

Team **CuentaMinds**'s submission to the *Claude Code Hackathon: Close the Books* (Talky × UCppM, 2026-11-07).
For every bank line of a 6-month phase, the engine decides which open items (customer/supplier invoices and credit
notes) it settles, for how much, and with which exception, following the organisers' `POLICIES.md` exactly.

> **New developer? Start with [`docs/BANKREC_GUIDE.md`](docs/BANKREC_GUIDE.md).** It covers the architecture, every stage,
> commands, the evaluation strategy, LLM agents, the submission pipeline, current status and the FINAL runbook.

## Results so far

| | score |
|---|---|
| organisers' naive starter | ~19 |
| our engine, last night (LIVE) | 79.55 |
| **our engine, LIVE leaderboard (live_v11)** | **90.81** |
| internal TRAIN-month evaluation (best commit) | 91.23 (mean of 2 held-out months) |

Score = 100 × (0.5 · allocation-pair F1 + 0.3 · line accuracy + 0.2 · exception macro-F1).

## How it works (one paragraph)

The engine is deterministic and policy-faithful: rules plus one small learned category table, with no LLM in the scoring path.
- **Exact batch-file joins first:** direct-debit batches, card settlements, cheque and lockbox deposits, payment runs
  (including FX transfers by end-to-end id), and confirming runs by beneficiary maturity.
- **Then per line, chronologically:** returns, reversals and advances; remittance advices (PDF/XLSX/CSV/TXT, including
  the short-number layout); document references; payer identification (IBAN + IDF-weighted names); and an exact subset
  search near the due date.
- **Policy exceptions:** discount window, bank charges, withholding, FX, partials and instalments, disputes, duplicates, returns.

A running open-item book carries balances across phases.

## Evaluation and agents

- **TRAIN-month evaluation** with the full open book and held-out models. It predicts the LIVE score within about 0.1–0.2
  (`bankrec_calibration.tsv`).
- **Karpathy-style autoresearch loop** (`scripts/autoresearch.py`, `docs/AUTORESEARCH.md`): one change → official score
  on 2 held-out months → automatic keep or revert. Includes a hold-out month and a static overfit guard.
- **Claude personas** (`scripts/analyst.py`, `claude-opus-5-5`, advisory only): an *analyst* that proposes experiments, a *skeptic*
  that reviews diffs for overfitting, and a *release manager* for the one-shot FINAL.
- **Submission pipeline:** `scripts/pipeline.py` (build → validate → upload via the platform API → score → per-segment
  report), the FINAL orchestrator `scripts/final_run.py`, and the label-free readiness gate `scripts/final_gate.py`.

## Quick start

```bash
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
# put the challenge data under data/ (git-ignored), then:
.venv/Scripts/bankrec train-nonitem
.venv/Scripts/bankrec run --phase sample --out submissions/sample.csv.gz         # ~1 min, prints the official score
.venv/Scripts/bankrec run --month 2025-03 --out submissions/m2025-03.csv.gz      # LIVE-like TRAIN-month evaluation
.venv/Scripts/python -m pytest -q
```

Secrets (`.env`, `claudeapi.txt`, the team token) and data are git-ignored and never committed.

## Repository layout

| path | content |
|---|---|
| `src/bankrec/` | the reconciliation engine (the submission) |
| `scripts/` | evaluation, analysis, agents, submission and FINAL tooling |
| `docs/` | developer guide, autoresearch protocol, earlier prototype docs |
| `tests/` | unit tests (bankrec + the earlier closeops prototype) |
| `src/closeops/`, `dashboard/` | earlier "finance close" prototype with a Gemini/Claude agent layer (background) |

Built with [Claude Code](https://claude.com/claude-code).
