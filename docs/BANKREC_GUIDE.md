# CuentaMinds — Bank Reconciliation Engine: Developer Guide

> **Who this is for:** a developer joining the team who must understand, run, evaluate, improve and submit this code.
> **State as of:** 2026-11-07 12:10 (hackathon day) · `main` = drift fix `c3731f3` + docs · experiment branch `autoresearch/nov7`.
> **Read order:** §1 (the problem) → §2 (setup) → §4 (architecture) → §8 (how we measure) → §10–11 (today's plan).
> **This guide is kept current.** Every kept change updates §9 (status) and §16 (changelog); see §15.

Repo: <https://github.com/Pratyush2586/CuentaMinds> (private until the hand-in) · Event site: <https://hackathon.usetalky.com/>

---

## Contents

1. [The challenge in one page](#1-the-challenge-in-one-page)
2. [Setup check (5 minutes)](#2-setup-check-5-minutes)
3. [Repository map](#3-repository-map)
4. [Architecture: how a submission is produced](#4-architecture-how-a-submission-is-produced)
5. [The engine stage by stage](#5-the-engine-stage-by-stage)
6. [Exceptions: how each one is decided](#6-exceptions-how-each-one-is-decided)
7. [Commands reference](#7-commands-reference)
8. [How we measure: evaluation methodology](#8-how-we-measure-evaluation-methodology)
9. [Current status, results and known gaps](#9-current-status-results-and-known-gaps)
10. [Hackathon day timeline](#10-hackathon-day-timeline)
11. [FINAL runbook (18:00 → 19:30)](#11-final-runbook-1800--1930)
12. [Project hand-in (video, repo, write-up)](#12-project-hand-in-video-repo-write-up)
13. [Troubleshooting and pitfalls](#13-troubleshooting-and-pitfalls)
14. [Rules we must never break](#14-rules-we-must-never-break)
15. [Keeping this guide current](#15-keeping-this-guide-current)
16. [Changelog](#16-changelog)

---

## 1. The challenge in one page

**Company:** *Grupo Valtermia*, a fictional group with 17 legal entities (one more in FINAL), 12 currencies, 13 banks and about 50 accounts.
**Task:** for every **bank line**, say **which open items it settles, for how much, and what was unusual**.

| item kind | meaning | usual bank sign |
|---|---|---|
| `ARI` | invoice we issued to a customer (receivable) | money **in** (+) |
| `ARC` | credit note we issued to a customer | nets against ARI / refund out |
| `API` | invoice a supplier sent us (payable) | money **out** (−) |
| `APC` | credit note from a supplier | nets against API / refund in |

A bank line can also settle **no item** (fees, payroll, taxes, loans, cash pooling…). Then the answer is a
**category** from `masters/categories.csv`. **Exceptions** (`PARTIAL`, `SHORT_DISCOUNT`, `SHORT_BANK_CHARGES`,
`SHORT_WITHHOLDING`, `SHORT_DISPUTE`, `OVERPAYMENT`, `DUPLICATE_PAYMENT`, `FX_DIFFERENCE`, `RETURNED`, `UNIDENTIFIED`)
follow `data/valtermia/docs/POLICIES.md` **exactly**.

**Submission:** one CSV (`.csv.gz` is fine), one row per allocation: `bank_txn_id,item_id,amount,category,exception`.
- Item row: `item_id` + `amount` in the **item's** currency.
- No-item row: `category` only.
- Every bank line of the phase must appear.

**Score** = 100 × (0.5 · allocation-pair F1 + 0.3 · line accuracy + 0.2 · exception macro-F1). Amount tolerance is
max(0.01, 0.5 %).
- *Pair F1* counts (bank line, item) pairs, so a payment of 60 invoices weighs 60×.
- *Macro-F1* gives each exception type equal weight, so rare exceptions matter.

| phase | months | golden? | submissions |
|---|---|---|---|
| sample | 2025-06 (docs zip) | yes | local only |
| TRAIN | 2021-07 → 2025-06 | yes | local only (**our main benchmark**, see §8) |
| LIVE | 2025-07 → 2025-12 | hidden | 20/day, ≥ 3 min apart, public leaderboard |
| FINAL | 2026-01 → 2026-06 | hidden | **one scored submission**, data at 18:00 today |

> ⚠️ **FINAL: the first valid file is your score.** A file rejected by validation does not count (fix it and re-upload).
> Ignore the site's "choose final" control. Upload exactly one file you have validated locally.

---

## 2. Setup check (5 minutes)

Run everything from the repo root `C:\Users\learn\Documents\prat\Hackathon\nova7nov` with the project venv
(`.venv/Scripts/python`, Python 3.12). The system `python` is 3.11 and is the wrong one.

```bash
cd /c/Users/learn/Documents/prat/Hackathon/nova7nov
git checkout main && git pull
.venv/Scripts/bankrec --help                 # train-nonitem, run, score
ls data/valtermia                            # cache docs live train (final appears at 18:00)
ls data/valtermia/cache/nonitem.json         # trained category model
.venv/Scripts/bankrec run --phase sample --out submissions/sample_check.csv.gz --note smoke   # ~1 min, "ok": true
```

On a fresh clone: `py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"`. Then
`.venv/Scripts/bankrec train-nonitem` (1–2 min). Data is never in git: download it from the site's **Datos** (Data) page.

**Secrets:** `.env` (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) and `claudeapi.txt` are git-ignored. Never print or commit them.
The Claude key is valid but the account has **no credit**. Nothing in bankrec uses an LLM.

### Data layout (git-ignored)

Folders are found **by content**: `data.find_phase_dir` looks for a folder named `sample|train|live|final` holding
`bank_transactions.csv.gz`. Extraction layout doesn't matter.

```
data/valtermia/
  docs/       README*, POLICIES.md, FORMAT.md, DATA_DICTIONARY.md, masters/, sample/ (with golden), starter/
  train/train/    4 years + golden_allocations.csv.gz (has a `segment` column per line: our error-analysis key)
  live/live/      LIVE inputs + open_items_at_start.csv.gz
  final/final/    tonight, + masters_final/
  cache/          nonitem.json, nonitem_ex_<month>.json, advices_*.json, month_<YYYY-MM>/{items,golden}.csv.gz
submissions/  generated files (git-ignored)      autoresearch/  experiment runs + results.tsv (git-ignored)
```

Each phase folder holds `bank_transactions`, `ar_invoices`, `ap_invoices`, `credit_notes`,
`open_items_at_start` (LIVE/FINAL), and the folders `direct_debit_batches/`, `card_settlements/`, `cheque_deposits/`,
`payment_runs/`, `remittance_advices/` (txt/csv/xlsx/pdf + `index.csv`) and `pdfs/`.

---

## 3. Repository map

The repo holds two projects. **Only `bankrec` is the hackathon submission.** `closeops` is an earlier "finance
close" prototype with a Gemini/Claude agent layer, kept for background (its ~300 tests still run in `pytest`).

| path | what |
|---|---|
| `src/bankrec/` | **the reconciliation engine** (below) |
| `scripts/autoresearch.py` | **experiment harness** (Karpathy-style loop, §8.3). Read-only during the loop |
| `scripts/bankrec_pairs.py` | where (bank line, item) pairs are lost, per golden segment |
| `scripts/bankrec_exc.py` | per-exception precision / recall / F1 |
| `scripts/bankrec_diff.py` | the wrong lines of one segment: bank text, golden-only rows, ours-only rows |
| `scripts/bankrec_probe.py` | look inside the engine for given lines (candidates, ranked parties, refs) |
| `scripts/submit.py` | **upload pipeline**: organisers' 4-step API (upload-url → S3 → score → poll 5 s). Validates locally first, 3-min spacing, daily quota, FINAL double confirmation; saves the scored JSON to `submissions/results/` |
| `scripts/live_report.py` | **LIVE feedback**: platform result vs previous upload vs TRAIN months, per segment, ranked by lines lost |
| `scripts/pipeline.py` | **end-to-end**: `bankrec run --phase live` → validate → (`--submit`) upload + score → report |
| `bankrec_scoreboard.md` | every `bankrec run` appends a line here (time, phase, file, score, note) |
| `docs/BANKREC_GUIDE.md` | this guide |
| `docs/AUTORESEARCH.md` | the experiment protocol the agent follows ("program.md") |
| `docs/GETTING_STARTED.md`, `USER_GUIDE.md`, `HACKATHON_PLAYBOOK.md` | closeops docs (background) |

### `src/bankrec/` file by file

| file | lines | responsibility |
|---|---|---|
| `cli.py` | 146 | `bankrec train-nonitem / run / score`. Pins `PYTHONHASHSEED=0` (re-executes itself) so runs are reproducible |
| `data.py` | 272 | loads a phase (`load`) or a **TRAIN month** (`load_train_month`): bank lines, item book with open amounts, masters, batch files |
| `engine.py` | 1194 | **the matcher**: stages, subset search, party identification, exception rules |
| `text.py` | 85 | `fold`, `norm`, `digits`, `tokens`, `bank_blob`, `name_key`, `DocIndex` (document-number lookup), `LEGAL` |
| `advices.py` | 144 | parses remittance advices (pdf/xlsx/csv/txt) into refs, total and per-invoice lines. Parallel and cached |
| `nonitem.py` | 77 | learned table: (sign, bank code, text shape) → category, trained on TRAIN golden |
| `submission.py` | 47 | writes the CSV; scores with the official scorer, or validates only when there's no golden |
| `official_score.py` | 354 | **verbatim** organisers' scorer. Never edit |

---

## 4. Architecture: how a submission is produced

```
 bankrec run --phase X  (or --month YYYY-MM for a TRAIN-month evaluation)
   │
   ├─ data.load / data.load_train_month
   │     bank lines · item book (ARI/ARC/API/APC, OPEN amount at period start) · masters
   │     batch files of the period + 4 months before (runs and batches settle later lines)
   ├─ advices.load_advices  → parsed remittance advices (cache/advices_*.json)
   ├─ NonItemModel.load     → cache/nonitem.json (or nonitem_ex_<month>.json for an evaluation)
   │
   ├─ Engine(data, model, advices).run()
   │     1. stage_batches: exact joins with batch files
   │          direct debits · card settlements · cheque deposits · payment runs · confirming
   │     2. lines sorted by booking date; within a day, lines quoting our document numbers first
   │        match_line(): return → advice → confident no-item → refs → party → credit refund → duplicate
   │        (every allocation updates the open-item book)
   │     3. no_item() for whatever is left: category, or UNIDENTIFIED
   │
   ├─ submission.write   → submissions/<file>.csv.gz
   ├─ submission.score   → official score (golden) or validation only (LIVE/FINAL)
   └─ bankrec_scoreboard.md   (unless --no-log)
```

### Design decisions (and why)

1. **Rules + one small learned table, no LLM in the scoring path.** About 200k lines must be decided in about 20 min, exactly and
   deterministically. Rules follow POLICIES.md literally. Only the no-item category is learned.
2. **Evidence ladder: exact first, fuzzy last.** Batch files and quoted document numbers are near-certain and run
   first. Name + amount guesses run last on what's left.
3. **A running open-item book.** `open` = amount still owed. Each allocation reduces it, and `RETURNED` re-opens it.
   Chronological processing stops a later line taking an already-paid invoice. Paid invoices from the last 6 months
   stay in the book with open 0, so a second payment is flagged `DUPLICATE_PAYMENT`.
4. **Reproducible.** The CLI pins the hash seed. The same code and data give byte-identical files. Without this, set
   iteration order changed a few tie-breaks per run (±0.05).
5. **Measured on what LIVE looks like.** See §8. The sample month was misleading (91 locally against 79.55 on LIVE).
6. **The official scorer is vendored**, so local numbers are exactly the organisers' formula, and LIVE/FINAL files get
   the official validation before upload.

### Inside the engine

`Engine.__init__` turns the item DataFrame into plain lists indexed by an integer `k` (`ids`, `kind`, `entity`,
`party`, `ccy`, `total`, `open`, `issue`, `due`, `doc`, `doc_norm`, `po`, `related`, `terms`) for speed.

**Lookups:**
- `pos[item_id]`
- `by_party[party_id]`
- `docidx` (document number → k)
- `iban_party`
- `word_parties` / `idf` (name words)
- `dd_by_mandate`
- `acct_entity`, `acct_ccy` (from `bank_accounts.csv`)
- `_terms` (payment terms → discount % and days)

**Results:** `result[bank_txn_id] = [Row(item, amount, category, exception), …]`, and `how[bank_txn_id] = stage`. The
run prints `by stage {...}` from `how`. A big jump between versions in one stage, for example `unidentified`, is a red flag.

---

## 5. The engine stage by stage

All in `src/bankrec/engine.py`. Search for the method name.

### Stage 1: batch files (`stage_batches`, `stage_confirming`)

| source | bank line | join | output |
|---|---|---|---|
| `direct_debit_batches/` | one credit = a SEPA collection batch | batch total + account + date ±7 d; fallback mandate / name / ref | ARI per collection |
| `card_settlements/` | acquirer payout | sum of `net` per settlement | ARI per order |
| `cheque_deposits/` | cheque / lockbox deposit | `deposit_total` | ARI per cheque (by stub ref, else drawer + amount) |
| `payment_runs/` transfers | one debit per end-to-end id | amount + account + date, **else the quoted e2e id** (`find_ref`) | API rows |
| `payment_runs/` aggregated | one debit per run | run total, **else the quoted run id** | API rows |
| `payment_runs/` confirming | a run debited **per maturity** over weeks | see below | API rows |

- **FX transfers and mixed-currency runs** are listed in the invoice currency but debited in the account currency, so
  the amount join fails. `find_ref` joins on the end-to-end or run id quoted in the bank text instead (`_RUN_REF`
  regex). PARTIAL and FX flags are set per document row in `payrun_rows`. The account currency comes from the
  **master**, because ids like `IT01-VALD-EUR-2` carry a numeric suffix.
- **Confirming:** one run (for example 79 beneficiaries) is debited over many days, one line per maturity date
  ("REMESA CF… 16 PROVEEDORES", i.e. 16 suppliers). A beneficiary's maturity is **the latest due date of its own
  invoices** (matched by IBAN or name, since supplier invoice numbers aren't unique), **rolled to the next business
  day**. The debit takes:
  1. the beneficiaries maturing on that date if they sum exactly;
  2. else an N-subset of those within ±3 days;
  3. else an N-subset of all of them (`_subset_count`).

### Stage 2: per-line matching (`run` → `match_line`)

Lines are processed by booking date. **Within a day, lines that quote one of our document numbers go first**, so an
unreferenced same-amount payment from the same payer can't take the invoice another line names.

1. `try_return`: a returned direct debit or cheque re-opens the original collection → `RETURNED`.
2. `try_advice`: a matching **remittance advice** (by ref, truncated ref + amount, or payer + amount + date).
   `advice_rows` turns its lines into rows (see §6 for deductions).
3. **Confident no-item:** the model is ≥ 90 % sure (≥ 20 examples) it's a fee, payroll, tax… → category.
4. **References:** `ref_items` → document numbers in the text → `resolve_for` over that payer family.
   An already-paid quoted invoice of the same amount → `DUPLICATE_PAYMENT`.
5. **Party:** `rank_parties` (IBAN first; then IDF-weighted name words, prefixes for truncated words, coverage ≥ 0.6;
   ties broken by exact full name and word order; coverage is **rounded to 6 decimals**, so a full match of
   0.9999999999 ties with 1.0 and the exact-name bonus decides) → `resolve_for` per party.
6. `try_credit_refund`: credit-note refunds.
7. `try_duplicate`: the amount equals an invoice already paid → `DUPLICATE_PAYMENT`.
8. `no_item`: the model's category. A receipt from a **known payer** (full-name coverage and open receivables)
   gets category `UNIDENTIFIED` with no exception. A receipt nobody can be tied to gets `UNIDENTIFIED / UNIDENTIFIED`.

### `resolve_for`: which open items a payment settles

Candidates = `open_of(family)` (open items on the right side, issued ≤ booking + 3 days). It tries in order and stops at
the first success:

1. quoted items
2. a subset of the quoted items
3. quoted items + credit notes
4. `_gap_fill`
5. a forced single item
6. `_settle_sub(all candidates)`
7. `settle_fx` (3 % tolerance)
8. `_explained_single` (one item with an allowed short)
9. `_withholding_multi`
10. `_overpaid_single` (round, 1 % or 10 % overpayment)

### `subset()`: which items add up to the cash

1. **A single exact item.** With several same-amount invoices, prefer the service month named in the text, else FIFO.
2. **An oldest-first prefix**, then a due-date prefix.
3. **`_near_sets`: a contiguous run of invoices** (by issue or by due date), else any **pair**, else any **triple**.
   With several solutions, it takes the one whose due dates are closest to the payment. This handles big open books
   (customers with 40–900 open items).
4. **A dictionary DP** over the 40 items due closest to the payment (60k-state cap).
5. `_complement`: everything except a few items.

### `settle()`

Turns a chosen set into rows and exceptions (§6). Overpayments are capped at max(10 %, 50).

---

## 6. Exceptions: how each one is decided

| exception | rule | row amount |
|---|---|---|
| *(none)* | paid = open (within tolerance) | open |
| `PARTIAL` | paid < open, no allowed short reason, item stays open (includes a discount taken **after** its window) | **paid** |
| `SHORT_DISCOUNT` | short = early-payment discount and booking ≤ issue + discount days + **3 grace days** | open |
| `SHORT_BANK_CHARGES` | short ≤ €60 and ≤ 2 %, foreign payment | open |
| `SHORT_WITHHOLDING` | customer in MX/BR/MA, short = 1.25 %, 1.5 % or 10 % | open |
| `SHORT_DISPUTE` | advice shows a deduction **the policy does not explain** | **paid** |
| `OVERPAYMENT` | paid > open, round / 1 % / 10 % signature | open |
| `DUPLICATE_PAYMENT` | the invoice was already settled | paid |
| `FX_DIFFERENCE` | invoice currency ≠ account currency, within 3 % after conversion; on **every** invoice row | open |
| `RETURNED` | returned debit or cheque re-opens the item | returned amount |
| `UNIDENTIFIED` | receipt nobody can be tied to | no-item row |

**Advice deductions** (`advice_rows`) are checked in this order:
1. A deduction the policy explains (discount in its window, withholding rate, bank charges) is **that** exception, and the row amount is the open amount.
2. A discount-shaped deduction outside the window is `PARTIAL`, with the paid amount.
3. Anything else is `SHORT_DISPUTE`, with the paid amount.

Before this fix, every deduction counted as a dispute, which put SHORT_DISCOUNT recall at 0.39.

The thresholds are constants at the top of `engine.py`: `FX_TOL`, `WITHHOLDING_RATES`, `WITHHOLDING_COUNTRIES`,
`OVERPAY_ROUND` and `MAX_SUBSET_ITEMS`. `short_reason()` picks which SHORT_* applies.

---

## 7. Commands reference

```bash
# category model
.venv/Scripts/bankrec train-nonitem                              # -> cache/nonitem.json (all TRAIN)
.venv/Scripts/bankrec train-nonitem --exclude-month 2025-03      # -> cache/nonitem_ex_2025-03.json (held out)

# runs (score with golden, or validate only; appends to bankrec_scoreboard.md unless --no-log)
.venv/Scripts/bankrec run --phase sample --out submissions/sample_vNN.csv.gz --note "what changed"
.venv/Scripts/bankrec run --month 2025-03 --out submissions/m2025-03_vNN.csv.gz --note "..."   # TRAIN-month evaluation
.venv/Scripts/bankrec run --phase live  --out submissions/live_vN.csv.gz --note "LIVE vN = ..."
.venv/Scripts/bankrec run --phase final --out submissions/final_v1.csv.gz --note "FINAL"
.venv/Scripts/bankrec score FILE --phase live            # re-validate a file

# error analysis (add --month M for a TRAIN month; without it they use the sample)
.venv/Scripts/python scripts/bankrec_pairs.py SUB --month 2025-03              # pair losses by segment
.venv/Scripts/python scripts/bankrec_exc.py   SUB --month 2025-03              # exception P/R/F1
.venv/Scripts/python scripts/bankrec_diff.py  SUB SEGMENT 10 --month 2025-03   # wrong lines of a segment
PYTHONHASHSEED=0 .venv/Scripts/python scripts/bankrec_probe.py 2025-03 BT-2025-0061782   # engine internals

# autoresearch loop (§8.3)
.venv/Scripts/python scripts/autoresearch.py baseline | eval "hypothesis" | holdout | status

# LIVE pipeline (token: HK_TOKEN env var or HK_TOKEN=... in the git-ignored .env; never printed)
.venv/Scripts/python scripts/pipeline.py --tag v10 --note "what changed" --submit   # build, validate, upload, score, report
.venv/Scripts/python scripts/submit.py LIVE submissions/live_v9.csv.gz --note "..."   # upload one validated file
.venv/Scripts/python scripts/live_report.py                                          # latest two platform results vs TRAIN
.venv/Scripts/python scripts/submit.py FINAL submissions/final_v1.csv.gz --i-confirm-final-is-one-shot   # FINAL, once

# emergency fallback (organisers' naive starter, score ≈ 19)
.venv/Scripts/python data/valtermia/docs/starter/starter.py --data data/valtermia/final --phase final --out submissions/final_starter.csv.gz
```

**Reading a run:** `load Ns match Ns lines N by stage {...}`, then `"ok": true` and, with a golden, the score and
per-segment accuracy.
- On LIVE/FINAL the printed score (20.0, F1 0) means nothing. Only `ok` matters.
- **Never upload without `"ok": true`.**

**Runtimes on this laptop:**

| run | time |
|---|---|
| sample | ~1 min |
| TRAIN month (first build of its cache: +2 min) | ~3.5 min |
| LIVE | **18 min alone**, 22 min when experiments share the CPU |
| FINAL (expected) | ~20–25 min, plus a few minutes of first-time PDF/advice parsing |

---

## 8. How we measure: evaluation methodology

### 8.1 Why not the sample month

The sample month has **8.5k lines and about 50k candidate items**. A real month has about 34k lines matched against
**about 1M open items**, so names and amounts are far more ambiguous. Last night's engine scored **91.38 on the sample**,
**80.25 on a realistic TRAIN month**, and **79.55 on LIVE**. The TRAIN month predicts LIVE; the sample doesn't.

### 8.2 The TRAIN-month evaluation (`run --month YYYY-MM`, `data.load_train_month`)

- The month's bank lines, scored against TRAIN's golden for those lines (cached in `cache/month_<M>/golden.csv.gz`).
- The **full open-item book at month start**, rebuilt from TRAIN's golden: total − allocations before the month,
  with `RETURNED` re-opening and full-clearing exceptions closing. This is the same idea as `open_items_at_start`.
  Items settled in the previous 6 months are kept with open 0.
- Batch files of the month and the 4 months before.
- Advices received from 35 days before the month to **20 days after month end** (LIVE/FINAL ship every advice of the
  phase, and advices often arrive after the payment).
- A category model **trained without that month** (`nonitem_ex_<M>.json`): no leakage.
- Dev months: **2025-03** and **2024-10**. Hold-out month: **2025-05** (never used for keep/discard decisions).

### 8.3 The autoresearch loop (Karpathy-style, ReAct)

Following [karpathy/autoresearch](https://github.com/karpathy/autoresearch). The protocol is in `docs/AUTORESEARCH.md`.

- **Agent:** Claude Code. **Observe** (pairs/exceptions/diff/probe) → **Think** (one-sentence hypothesis grounded in
  POLICIES.md) → **Act** (one generic change, `git commit -m "exp: …"`) → **Evaluate**:
  `python scripts/autoresearch.py eval "<hypothesis>"`. That runs both dev months in parallel, about 4 min.
- **Keep rule:** the mean score gains ≥ 0.02 **and** no dev month drops more than 0.30. Otherwise the harness undoes
  the `exp:` commit itself (`git reset --soft HEAD~1` + restore the engine files).
- **Log:** `autoresearch/results.tsv` (git-ignored) with commit, mean, per month, match seconds, status and description.
- **Branch:** `autoresearch/nov7`. Kept commits are fast-forwarded to `main` (`git checkout main && git merge --ff-only
  autoresearch/nov7 && git push`), then LIVE is re-run and uploaded.
- **Editable:** `engine.py`, `text.py`, `advices.py`. **Read-only:** the harness, `official_score.py`,
  `submission.py`, `data.load_train_month`. A change to the evaluation itself is a separate `fix(eval):` commit,
  followed by a new **baseline**, as with the advice window on 2026-11-07.

---

## 9. Current status, results and known gaps

### 9.1 Status (2026-11-07 11:50)

| item | state |
|---|---|
| best engine | `main` = `43b9049` (= experiment `8469efa` + log). **TRAIN-month mean 90.55** (2025-03: 91.46, 2024-10: 89.64) |
| LIVE leaderboard | CuentaMinds **87.90** (live_v8), 2nd. Leader: Exponential 90.69. (live_v7 87.56, live_v4 79.55) |
| LIVE v8 breakdown | pair F1 89.9 % (P 97.4, R 83.4) · line accuracy 91.1 % · exception macro-F1 78.1 % (results in `submissions/results/`) |
| next upload | `live_v9` = 90.55 engine + Sept-2025 advice-layout drift fix (§9.5) |
| repo | pushed to GitHub (private) |

### 9.2 Score history

| version | sample | TRAIN 2025-03 | TRAIN 2024-10 | LIVE | change |
|---|---|---|---|---|---|
| starter | 11.28 | | | | organisers' naive baseline |
| v1 | 58.79 | | | | batches, refs, party matching, category model |
| v10 | 84.03 | | | | returns, credit notes, subset search |
| v21 | 91.38 | 80.25 | | **79.55** (live_v4) | duplicate payments of already-settled invoices |
| v22 | 91.49 | 83.54 | | | account currency from master; subset search near the due date; exact-name tie |
| v23 | 91.47 | 86.06 | | | payment runs joined by quoted e2e/run id (FX transfers) |
| v25 | 91.75 | 89.33 | 87.12 | live_v6 | advice deductions explained by policy are not disputes |
| *eval fix* | | 91.17 | 89.37 | | advice window +20 days (re-baseline; v25 + confirming) |
| exp 3b | | (89.42) | (87.32) | **87.56** (live_v7) | confirming by beneficiary maturity, next business day |
| exp 6 | | 91.43 | 89.60 | | party-name coverage rounding (float tie bug) |
| **exp 7** | | **91.46** | **89.64** | **87.90** (live_v8) | within a day, referenced lines first |
| drift fix | | 91.46 | 89.64 | live_v9 | advice short invoice numbers / PO (LIVE-only layout, neutral on TRAIN) |

### 9.3 Autoresearch log (2026-11-07)

| commit | Δ mean | status | hypothesis |
|---|---|---|---|
| d12a8f0 | 88.225 | baseline | v25 |
| f3b8669 | +0.010 | discard | truncated refs → global prefix lookup |
| 9af7f21 | 0.000 | discard | confirming: order beneficiaries by maturity |
| c546cf3 | 0.000 | discard | confirming: due date rolled to business day (supplier invoice numbers not unique) |
| 42c3dd8 | +0.145 | **keep** | confirming: the beneficiary's **own** latest due date, next business day |
| 5162bae | 90.270 | baseline | eval fix: advices window +20 days |
| 1eaab8b | +0.010 | discard | credit note + related invoice as one unit (near-sets only) |
| bf7fbc1 | −0.185 | discard | credit-note units through the whole subset pipeline (pre-empts better singles) |
| dea6636 | +0.245 | **keep** | round name coverage (0.9999999 lost ties to partial names) |
| 8469efa | +0.035 | **keep** | same-day ordering: referenced lines first |
| 176dadf | +0.010 | discard | lookalike payers by legal form (also caught the word "Spa") |
| c852e32 → c3731f3 | +0.000 | **kept as drift fix** | advice short invoice numbers / PO lookup. LIVE advice lines resolved 88.3 % → 97.9 % |
| (earlier) | −0.50 | rejected | exact bitset subset-sum fallback for known payers: too many spurious exact sets |

### 9.4 Known gaps (TRAIN 2025-03, best engine), biggest first

| gap | size | notes / ideas |
|---|---|---|
| `ar_ref_noise` | 3.3k missed pairs | truncated names/refs. Remaining failures are mostly "known payer, unmatched" |
| `ar_1toN_credit_note` | 2.3k | 95 % of credit notes are paid with the invoice their `related_document` names; units help only for small sets so far |
| `ar_short_dispute` | 2.1k | payers with up to 900 open items paying 60 invoices with 4 disputes (3/5/30 %): out of reach today |
| `ar_n_to_m`, `ar_1toN_text` | 0.8k each | many-invoice payments with partial refs |
| `ap_confirming` | 0.7k | maturity rule leaves some debits (N ≠ count at the date) |
| UNIDENTIFIED F1 0.69 | 300 lines | lookalike payers counted as known (category right, exception missing) |
| PARTIAL recall 0.70 | | partial payments not recognised |
| DUPLICATE_PAYMENT P 0.40–0.67 | | a wrong earlier allocation (often a DD batch) closes the invoice first |

### 9.5 LIVE vs TRAIN: drift found on 2026-11-07

LIVE v7 scored 87.56 against our evaluation's ~90.3. Per segment, AP did *better* on LIVE but several AR segments did much worse:

| segment | LIVE | TRAIN avg |
|---|---|---|
| ar_n_to_m | 19.5 % | 45 % |
| ar_partial | 74.7 % | 85 % |
| ar_instalment | 70.0 % | 78 % |
| ar_1toN_text | 89.4 % | 93 % |

Cause: **the Sept-2025 remittance-advice layout change** (POLICIES §6). Many customers moved from csv to an xlsx
`Voucher | Invoice | PO | Inv. date | Amount | Deduction | Net paid`. The "Invoice" column prints a *short* number
(`430845` for `1072430845`) or is empty (PO only). Only 88 % of post-September advice lines resolved, against 98 % before.
`_adv_doc` now falls back to:
1. a suffix of the payer's own receivables;
2. the customer PO;
3. a suffix plus the same gross amount anywhere in the entity.

That gives 97.9 %. TRAIN can't show this, so it's kept under the **drift-fix rule** (`docs/AUTORESEARCH.md`).
**Lesson:** for LIVE/FINAL-only behaviour, measure on the LIVE inputs directly, not just on TRAIN months.

### 9.6 FINAL drift to watch (POLICIES.md §6)

- **New subsidiary ZA01** from 2026-01-01: new bank, ZAR, invoice numbers "KWI…", renamed 2026-04-01.
  `final/masters_final/` must be picked up (`data.find_masters`). The current `fx_rates.csv` stops at 2025-12-31.
- Banco del Moncayo has used camt.053 since 2025-10-01 (already the case in LIVE). Customers rename and change banks; payments are slower in 2026.

---

## 10. Hackathon day timeline

Times are **Madrid**. The server deadlines are: FINAL data at **18:00**, submissions close **19:30**, projects close **19:45**, reveal **20:30**.

| time | action |
|---|---|
| now → 15:30 | autoresearch loop. After each kept batch: fast-forward `main`, run LIVE, upload (≥ 3 min apart, ≤ 20/day) |
| **15:30** | **code freeze.** `git checkout main && git tag final-engine && git push --tags` |
| 15:30–17:30 | video, write-up, presentation (§12) |
| 17:30 | **stop all experiments** (they slow FINAL). Clean `git status` on `main`. Laptop on power |
| **18:00** | FINAL → §11 |
| ~18:45 | upload FINAL **once** |
| 19:00 | make the repo public, fill in the Project page, **Save** |

---

## 11. FINAL runbook (18:00 → 19:30)

> Goal: one **valid** file uploaded by **~18:45**. Run on `main` at the `final-engine` tag, with nothing else running.

1. **Download and verify:** download FINAL from **Datos** (Data) into `data/`, then `sha256sum data/bankrec-final.tar.gz` must equal the hash on the page.
2. **Extract:** `mkdir -p data/valtermia/final && tar -xzf data/bankrec-final.tar.gz -C data/valtermia/final`.
   Check `find data/valtermia/final -maxdepth 3 -name bank_transactions.csv.gz` and that `masters_final` exists.
3. **Sanity check (2 min):**
   ```bash
   .venv/Scripts/python - <<'EOF'
   from bankrec import data as D
   d = D.load("data/valtermia", "final")
   print("lines", len(d.txns), d.txns.booking_date.min(), d.txns.booking_date.max())
   print("masters from", d.masters_dir)
   print("entities", sorted(d.entities.entity.unique()))
   print("ZAR in fx", "ZAR" in set(d.fx.currency), "| fx up to", d.fx.date.max())
   print("open items", int((d.items.open > 0).sum()), "| dd/cards/cheques/payruns", len(d.dd), len(d.cards), len(d.cheques), len(d.payruns))
   EOF
   ```
   Expect about 220k lines from 2026-01-01 to 2026-06-30, `masters_final`, ZA01 among the entities, and fx up to 2026-06. On LIVE this prints
   203,083 lines, 17 entities and fx up to 2025-12-31.
4. **Run:** `.venv/Scripts/bankrec run --phase final --out submissions/final_v1.csv.gz --note "FINAL = engine $(git rev-parse --short HEAD)"`.
   It stays quiet for the first minutes while PDFs and advices are parsed. Don't kill it.
5. **Check:** `"ok": true`. Stage counts should be proportional to LIVE v7: about 203k lines, payrun ~54k, party ~60k, ref ~39k,
   unidentified ~2.5k. **Tens of thousands of unidentified means something didn't load.** Then run
   `bankrec score submissions/final_v1.csv.gz --phase final` again and check the size is < 60 MB.
6. **Upload once.** Pratyush uploads it on **Enviar** (Submit). If validation rejects it, fix it and re-upload. Once a file is accepted, stop.
   Then `git add bankrec_scoreboard.md && git commit -m "chore: FINAL logged" && git push`.

| problem | action |
|---|---|
| traceback | read the last line. Most likely a new value (ZA01, ZAR, new column). Add a guard and re-run. Not fixed by **19:05** → fallback |
| still matching at 19:00 | let it finish; generate the fallback in parallel |
| `"ok": false` | the printed errors say what (missing lines, bad category, amount). Fix and re-score |
| **19:10, no valid file** | starter fallback (§7), validate it, upload (≈ 19 points, better than nothing) |

---

## 12. Project hand-in (video, repo, write-up)

On the **Proyecto** (Project) page before **19:45**. Only the organisers and the jury see it. The jury judges the approach as well as the score.

- [ ] **Video**: about 5 min at 720p (mp4/mov/webm ≤ 1 GB), or an unlisted YouTube/Loom/Drive link that opens without signing in
- [ ] **Repo**: `https://github.com/Pratyush2586/CuentaMinds`. Make it public:
      `"C:\Program Files\GitHub CLI\gh.exe" repo edit Pratyush2586/CuentaMinds --visibility public --accept-visibility-change-consequences`.
      Tick "Repo access confirmed"
- [ ] **Write-up** (template below), then **Save** and check that "Saved …" appears
- [ ] FINAL submitted (§11)

**Write-up template:**

```
CuentaMinds: policy-faithful, measured bank reconciliation for Grupo Valtermia

Key ideas
- Evidence ladder: exact batch-file joins (SEPA direct debits, cards, cheques, payment runs incl. FX transfers by
  end-to-end id, confirming runs by beneficiary maturity) -> returns -> remittance advices (PDF/XLSX/CSV/TXT) ->
  document references -> payer identification (IBAN + IDF-weighted names) -> learned no-item categories.
- Running open-item book, chronological, carried across phases (duplicate payments of settled invoices).
- Exact subset search near the due date (contiguous runs, pairs, triples, capped DP) for payers with big open books.
- POLICIES.md implemented literally (discount window + grace, bank charges, withholding, 3% FX, partial vs dispute).
- Honest evaluation: TRAIN months rebuilt with the full open book and held-out models; it predicted LIVE within 1 point
  (80.25 vs 79.55) while the sample month said 91.
- Karpathy-style autoresearch: the agent proposes one change, the harness scores two held-out months and keeps or
  reverts it automatically (results.tsv).

Results
- Sample 11.3 (starter) -> 91.75. Realistic TRAIN month 80.25 -> 91.46. LIVE 79.55 -> <fill in>.

Stack and models
- Python 3.12, pandas, pdfplumber, openpyxl; vendored official scorer. No LLM in the scoring path.
- Built with Claude Code (engine, analysis tooling, autoresearch loop, docs).

What we would do next
- Contract clauses from PDFs (instalments, retentions, advances, cancel-and-replace); many-to-many payments; disputes.

Team: CuentaMinds: <names>
```

**Video outline:**

| time | content |
|---|---|
| 0:00 | the problem and the score formula |
| 0:30 | architecture (§4) |
| 2:00 | live demo: `bankrec run --month 2025-03` and `scripts/autoresearch.py status` |
| 3:00 | score history (§9.2): sample vs TRAIN-month vs LIVE |
| 4:00 | FINAL drift and what's next |

---

## 13. Troubleshooting and pitfalls

| problem | fix |
|---|---|
| `bankrec: command not found` | `.venv/Scripts/bankrec` (or `.venv/Scripts/python -m bankrec.cli`) |
| result page crashes: "Failed to execute 'insertBefore' on 'Node'" | the **browser** broke the page, usually Chrome auto-translate or an extension. The upload still counts. Turn off translation for the site, or use Incognito |
| `FileNotFoundError: no final/...` | FINAL isn't extracted under `data/` |
| two runs give different files | run through the `bankrec` CLI (it pins `PYTHONHASHSEED=0`). Ad-hoc scripts need `PYTHONHASHSEED=0` |
| a regex suddenly matches nothing after a shell edit | heredocs can turn `\b` into a backspace byte (0x08). Check with `grep -c $'\x08' src/bankrec/engine.py`; fix by replacing the byte with `\b` |
| LIVE run very slow | experiments running at the same time. Stop them for LIVE/FINAL |
| `git push` auth error | `"C:\Program Files\GitHub CLI\gh.exe" auth setup-git` (account Pratyush2586) |
| autoresearch says "commit your engine change first" | `git commit -am "exp: ..."` before `eval` |
| a discarded experiment wasn't reverted | the HEAD subject must start with `exp:`; otherwise reset by hand |
| `UnicodeEncodeError` in your own script | `sys.stdout.reconfigure(encoding="utf-8")` (note that `python -I` ignores `PYTHONIOENCODING`) |
| Claude API: "credit balance is too low" | the account needs credit at console.anthropic.com. Not needed for bankrec |

---

## 14. Rules we must never break

1. **Never share or commit the team token**, API keys (`.env`, `claudeapi.txt`) or `data/`. Only Pratyush uploads.
2. **Fair play:** no attempts to get the LIVE/FINAL golden, and no probing of the platform. Improvements come from TRAIN data and POLICIES.md.
3. **No logic keyed to specific ids, customers, amounts or dates.** Generic rules only.
4. **Never upload a file without a local `"ok": true`.** FINAL: upload **once**.
5. **Measure on TRAIN months, not the sample.** One change per experiment. Keep only what the harness keeps.
6. All code, comments, docs and commit messages are in **English**. Spanish appears only as data patterns the engine must
   recognise (bank-text and PDF keywords, month names), with English comments.
7. Every run is logged (`bankrec_scoreboard.md`, `autoresearch/results.tsv`). Commit after each kept change.

---

## 15. Keeping this guide current

This guide is part of the code. **Update it in the same commit as the change** whenever:

| what changed | update |
|---|---|
| an experiment is **kept** / promoted to `main` | §9.1 status, §9.2 score history, §9.3 log, §16 changelog. §5/§6 if behaviour changed |
| a new LIVE score comes in | §9.1 and §9.2 (LIVE column) |
| the evaluation changes (`fix(eval)`) | §8.2 and a re-baseline row in §9.2/§9.3 |
| a new command, flag or script | §3 and §7 |
| a new failure mode or fix for one | §13 |
| a new gap is found, or one is closed | §9.4 |

Discarded experiments are recorded only in §9.3 (one line), so nobody tries them again.

---

## 16. Changelog

| date / time | commit | change |
|---|---|---|
| 11-06 | 42fc572 | v21: duplicate payments of settled invoices (sample 91.38) |
| 11-06 | 038f6ef | first version of this guide + FINAL runbook |
| 11-07 10:05 | 38fb46e | TRAIN-month evaluation (`--month`, `--exclude-month`); payment-run account currency from master; e2e/run-id joins; near-set subset search; exact-name tie; reproducible runs; analysis scripts |
| 11-07 10:35 | 8f4efe3 | advice deductions explained by policy are discount/withholding/charges, not disputes |
| 11-07 10:50 | 96fbcb7 | autoresearch harness + protocol (`scripts/autoresearch.py`, `docs/AUTORESEARCH.md`), `--no-log` |
| 11-07 11:05 | 42c3dd8 | confirming debits matched by beneficiary maturity (next business day) |
| 11-07 11:10 | 5162bae | eval: advices window +20 days past month end; re-baseline 90.27 |
| 11-07 11:30 | dea6636 | party-name coverage rounded (float tie bug) |
| 11-07 11:40 | 8469efa | within a day, referenced lines matched first |
| 11-07 11:50 | f72c002 | guide rewritten to current state |
| 11-07 12:10 | c3731f3 | LIVE v7 = 87.56; drift fix for the Sept-2025 advice layout; drift-fix rule in AUTORESEARCH.md |
| 11-07 12:25 | – | LIVE v8 = 87.90; upload + feedback pipeline (`submit.py`, `live_report.py`, `pipeline.py`) |
