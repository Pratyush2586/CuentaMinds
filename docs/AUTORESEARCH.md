# autoresearch: bankrec

An experiment loop in which the coding agent (Claude Code) improves the reconciliation engine on its own, following
[karpathy/autoresearch](https://github.com/karpathy/autoresearch). It works as a **ReAct** loop:
**Thought** (read the observation) → **Action** (one code change) → **Observation** (official score + diagnostics) →
keep or reset → repeat.

This file is the agent's instructions. The human edits it; the agent follows it.

## Setup

1. **Branch**: work on `autoresearch/<tag>` (e.g. `autoresearch/nov7`), created from `main`.
2. **Read the in-scope files**: `docs/BANKREC_GUIDE.md` §4–6, `src/bankrec/engine.py`, `data/valtermia/docs/POLICIES.md`.
3. **Check the data**: the TRAIN data must be present, i.e. `data/valtermia/train/train/bank_transactions.csv.gz` exists.
   The harness builds the month caches and held-out category models on first use, which takes a few minutes.
4. **Baseline**: `.venv/Scripts/python scripts/autoresearch.py baseline` evaluates the untouched engine and writes the
   first `keep` row of `autoresearch/results.tsv`.
5. Go.

## Experimentation

Each experiment is evaluated on the **dev months** (`DEV_MONTHS` in the harness, currently `2025-03` and `2024-10`).
Each one is a TRAIN month with the **full open-item book** at month start, rebuilt from TRAIN's golden, and a category
model trained **without** that month. This is the setting that matches LIVE: last night's engine scored 80.25 on
2025-03 and **79.55 on the LIVE leaderboard**. The sample month (8.5k lines, small book) is too easy and is not the metric.

**Goal: the highest `score_mean`** (mean official score over the dev months).

**What you CAN do**
- Edit `src/bankrec/engine.py`, `src/bankrec/text.py` and `src/bankrec/advices.py`: matching stages, subset search,
  party identification, exception rules, thresholds.

**What you CANNOT do**
- Modify `scripts/autoresearch.py`, `src/bankrec/official_score.py`, `src/bankrec/submission.py`, or the TRAIN-month
  loader `data.load_train_month`. They are the ground truth.
- Key logic on specific ids, customers, amounts or dates from the data. Rules must be generic, as POLICIES.md is.
- Read LIVE or FINAL answers (there are none) or touch the platform. Fair play.
- Add dependencies.

**Simplicity criterion**: all else being equal, simpler is better. A +0.02 that adds 40 lines of special cases isn't
worth it. Deleting code with an equal score is a win.

**Speed constraint**: a dev month must match in **≤ 6 min** (`match_s`). LIVE and FINAL are about 7 months of lines
and must finish in about 25 min. That matters most at 18:00 tonight.

**The first run** is always the baseline.

## Output format

The harness prints:

```
---
commit:      38fb46e
score_mean:  87.700
score_2025-03: 89.33  (ok, match 241s)
score_2024-10: 86.07  (ok, match 230s)
vs best 8f4efe3 (87.650): gain +0.050, worst month drop +0.10 -> KEEP
--- observation (dev month 1) ---
metrics: allocation_f1 ... txn_accuracy ... exception_macro_f1 ...
<per-exception P/R/F1>
<pair losses per segment, biggest first>
```

## Logging results

`autoresearch/results.tsv` is tab-separated and git-ignored. The harness writes it; never commit it.

```
commit  score_mean  score_2025-03  score_2024-10  match_s  status  description
```

`status` is `keep`, `discard` or `crash`.

## The experiment loop

LOOP until the human stops you, or until **17:30** on FINAL day:

1. **Observe.** Read the last observation, or `scripts/bankrec_pairs.py` / `bankrec_exc.py` / `bankrec_diff.py SUB
   SEGMENT N --month M`, and pick the **biggest loss you can explain from POLICIES.md**.
2. **Think.** State the hypothesis in one sentence: "lines of segment X fail because Y; rule Z fixes it".
   Use `scripts/bankrec_probe.py MONTH TXN...` to look inside the engine for a few failing lines.
3. **Act.** Make ONE change. Then `git commit -am "exp: <hypothesis>"`.
4. **Evaluate.** `.venv/Scripts/python scripts/autoresearch.py eval "<hypothesis>"`. Takes about 5 min; both months run in
   parallel.
5. The harness decides:
   - **keep**: score_mean gain ≥ 0.02 and no dev month drops more than 0.30. The commit stays.
   - **discard**: the experiment commit is undone automatically.
   - **crash**: read the traceback in the observation. A typo gets fixed and re-run. A broken idea gets discarded.
6. Every 3–4 kept experiments: run `scripts/autoresearch.py holdout` (month `2025-05`, never used for decisions) and the
   sample. If the hold-out falls while the dev months rise, you are overfitting: revert to the last commit that held up.
7. **Promote**: when a kept commit also holds up on the hold-out, merge it to `main`, run LIVE
   (`bankrec run --phase live`), and give the file to the human to upload.

**Timeout**: a run over 12 minutes is killed and counts as a crash. Too slow for FINAL.

**Never stop to ask** whether to continue. When out of ideas, re-read POLICIES.md §3–5, look at the next segment in the
pair-loss table, or combine near-misses from the discard rows.

## Ideas backlog (from the analysis on 2026-11-07)

| loss (2025-03) | idea |
|---|---|
| ar_short_dispute: 4.6k pairs | big multi-invoice payments with one disputed invoice: refs quoted (some truncated) + gap = a dispute short |
| ar_ref_noise: 3.5k pairs | truncated doc refs (`INV25-01025` → `INV25-0102581`): global prefix lookup on the doc index |
| ar_1toN_credit_note: 2.4k pairs | invoices netted with a credit note, from payers with big open books |
| ap_confirming: 1.0k pairs | runs debited per maturity date across months: match beneficiaries by maturity, not by an N-of-79 subset |
| UNIDENTIFIED F1 0.69 | known-payer vs unknown boundary |
| tried, rejected | exact bitset subset-sum fallback for known payers (86.06 → 85.56, too many spurious sets) |
