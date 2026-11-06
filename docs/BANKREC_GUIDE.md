# CuentaMinds — Bank Reconciliation Engine: Developer & Hackathon-Day Guide

> **Who this is for:** an entry-level engineer who has never seen this code and must run, understand, improve and
> submit it on hackathon day (**Saturday 2026-11-07**).
> **Read order:** §1 (what we are doing) → §2 (setup check) → §10 (tomorrow's timeline) → the rest when you need it.

Repo: <https://github.com/Pratyush2586/CuentaMinds> (private until hand-in) · Event site: <https://hackathon.usetalky.com/>

---

## Contents

1. [The challenge in one page](#1-the-challenge-in-one-page)
2. [Setup check (5 minutes)](#2-setup-check-5-minutes)
3. [Repository map](#3-repository-map)
4. [Architecture: how a submission is produced](#4-architecture-how-a-submission-is-produced)
5. [The engine stage by stage](#5-the-engine-stage-by-stage)
6. [Exceptions: how each one is decided](#6-exceptions-how-each-one-is-decided)
7. [Commands reference](#7-commands-reference)
8. [How to improve the score safely (dev loop)](#8-how-to-improve-the-score-safely-dev-loop)
9. [Where we are now and what is still weak](#9-where-we-are-now-and-what-is-still-weak)
10. [**Hackathon day timeline (tomorrow)**](#10-hackathon-day-timeline-tomorrow)
11. [**FINAL runbook (18:00 → 19:30)**](#11-final-runbook-1800--1930)
12. [Project hand-in (video, repo, write-up)](#12-project-hand-in-video-repo-write-up)
13. [Troubleshooting](#13-troubleshooting)
14. [Rules we must never break](#14-rules-we-must-never-break)

---

## 1. The challenge in one page

**Company:** *Grupo Valtermia*, a fictional group with 17 legal entities (one more in FINAL), 12 currencies, 13 banks.
**Task:** for every **bank line** (a movement on a bank statement), say **which open items it settles, for how much,
and whether anything unusual happened**.

- **Items** are the things a bank line can settle:

  | kind | meaning | usual bank sign |
  |---|---|---|
  | `ARI` | invoice we issued to a customer (accounts receivable) | money **in** (+) |
  | `ARC` | credit note we issued to a customer | nets against ARI / refund out |
  | `API` | invoice a supplier sent us (accounts payable) | money **out** (−) |
  | `APC` | credit note a supplier gave us | nets against API / refund in |

- **A bank line can also settle no item**: bank fees, salaries, taxes, loans, intra-group transfers… Then we
  answer with a **category** (from `masters/categories.csv`) instead of an item.
- **Exceptions** flag what was unusual: `PARTIAL`, `SHORT_DISCOUNT`, `SHORT_BANK_CHARGES`, `SHORT_WITHHOLDING`,
  `SHORT_DISPUTE`, `OVERPAYMENT`, `DUPLICATE_PAYMENT`, `FX_DIFFERENCE`, `RETURNED`, `UNIDENTIFIED`.
  The exact rules are in `data/valtermia/docs/POLICIES.md`; the labels follow them **exactly**.

### Submission file

One CSV (gzip allowed), one row per allocation, columns:

```
bank_txn_id,item_id,amount,category,exception
```

- Item row: `item_id` + `amount` (in the **item's** currency), `category` empty.
- No-item row: `item_id` empty, `amount` empty, `category` filled.
- Every bank line of the phase must appear at least once. Missing lines count as wrong.

### Scoring

```
Score = 100 × ( 0.5 · allocation-pair F1  +  0.3 · per-line accuracy  +  0.2 · exception macro-F1 )
```

- **Pair F1:** each `(bank line, item)` pair we output vs. the golden pairs.
- **Line accuracy:** a bank line is right only if *all* its rows (items, amounts, category, exception) are right.
- **Exception macro-F1:** the average F1 across exception types, so rare exceptions count as much as common ones.
- Amounts match if within `max(0.01, 0.5 %)`.
- Reference points: naive ≈ 19, good heuristics ≈ 61. **Our engine: 91.38 on the sample month.**

### Phases

| phase | months | golden? | submissions |
|---|---|---|---|
| sample | 2025-06 (1 month, in the docs zip) | **yes** | local only (our dev benchmark) |
| TRAIN | 2021-07 → 2025-06 | **yes** | local only |
| LIVE | 2025-07 → 2025-12 | hidden | 20/day, ≥ 3 min apart, public leaderboard |
| FINAL | 2026-01 → 2026-06 | hidden | **ONE scored submission**, released 18:00 tomorrow |

> ⚠️ **FINAL = one scored submission.** A file *rejected by validation* does not count (you can fix it and re-upload),
> but the **first valid file is your score**. Ties go to the earlier submission. The site's Submit page has a
> "choose your final" control; **do not rely on it. Upload exactly one file that you have validated locally.**

---

## 2. Setup check (5 minutes)

Everything runs from the repo root `C:\Users\learn\Documents\prat\Hackathon\nova7nov` in **Git Bash** or
**PowerShell**. Always use the project's Python 3.12 venv: `.venv/Scripts/python`. The system `python` is 3.11 and is the wrong one.

```bash
cd /c/Users/learn/Documents/prat/Hackathon/nova7nov
git pull                                   # get the latest engine
.venv/Scripts/python --version             # Python 3.12.x
.venv/Scripts/bankrec --help               # shows: train-nonitem, run, score
ls data/valtermia                          # cache  docs  live  train   (final appears tomorrow)
ls data/valtermia/cache                    # nonitem.json must be there (the trained category model)
```

If `.venv` is missing (fresh clone on another laptop):

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # installs closeops + bankrec entry points
```

If `data/valtermia/cache/nonitem.json` is missing: `.venv/Scripts/bankrec train-nonitem` (≈ 1–2 min, reads TRAIN).

**Smoke test (≈ 3–4 min):** proves everything works end to end and prints the sample score:

```bash
.venv/Scripts/bankrec run --phase sample --out submissions/sample_check.csv.gz --note "smoke"
# expect: "ok": true, "score": ~91.4
```

### Data layout

The archives were extracted where they landed, so folders are **found by content**, not by fixed path
(`data.find_phase_dir` looks for a folder named `sample|train|live|final` that holds `bank_transactions.csv.gz`).

```
data/                                   (git-ignored: never commit)
  bankrec-docs-and-masters.zip          original downloads
  bankrec-train.tar.gz / -train-lite / bankrec-live.tar.gz
  valtermia/
    docs/      README*.md POLICIES.md FORMAT.md DATA_DICTIONARY.md
               masters/      customers, suppliers, categories, payment_terms, fx_rates, bank_accounts, entities
               sample/       the 1-month sample WITH golden_allocations.csv.gz
               starter/      starter.py (naive baseline), bankrec_score.py (official scorer)
    train/train/   4 years with golden
    live/live/     LIVE inputs + open_items_at_start.csv.gz (no golden)
    final/final/   ← tomorrow 18:00 (+ masters_final/)
    cache/     nonitem.json, parsed remittance advices (advices_*.json), sample opening balances
submissions/   every file we generated (git-ignored)
```

Each phase folder holds: `bank_transactions.csv.gz`, `ar_invoices`, `ap_invoices`, `credit_notes`,
`open_items_at_start` (LIVE/FINAL), and the sub-folders `direct_debit_batches/`, `card_settlements/`,
`cheque_deposits/`, `payment_runs/`, `remittance_advices/` (txt/csv/xlsx/pdf + `index.csv`) and `pdfs/`.

---

## 3. Repository map

The repo holds **two projects**. Only `bankrec` matters for tomorrow.

| path | what | tomorrow? |
|---|---|---|
| `src/bankrec/` | **the bank-reconciliation engine (this guide)** | ✅ |
| `scripts/bankrec_*.py` | error-analysis helpers | ✅ |
| `bankrec_scoreboard.md` | log of every run (auto-appended) | ✅ |
| `submissions/` | generated CSVs (ignored by git) | ✅ |
| `src/closeops/`, `dashboard/`, `config/`, `tests/` | earlier "finance close" prototype with AI-agent layer (Gemini/Claude) | ❌ background only |
| `docs/GETTING_STARTED.md`, `USER_GUIDE.md`, `HACKATHON_PLAYBOOK.md` | closeops docs | ❌ |

### `src/bankrec/` file by file

| file | lines | responsibility |
|---|---|---|
| `cli.py` | 124 | `bankrec` command: `train-nonitem`, `run`, `score`; writes the scoreboard line |
| `data.py` | 210 | finds folders, loads bank lines, items, masters, batch files; builds the **open-item book** |
| `engine.py` | 1082 | **the matcher**: all stages, amount/subset logic, exception rules |
| `text.py` | 85 | text normalisation: `fold`, `norm`, `digits`, `bank_blob`, `name_key`, `DocIndex` (document-number lookup) |
| `advices.py` | 141 | reads remittance advices (pdf/xlsx/csv/txt), extracts refs, totals, per-invoice lines; parallel + cached |
| `nonitem.py` | 77 | learned model: bank code + text shape → category (trained on TRAIN golden) |
| `submission.py` | 47 | writes the CSV; calls the official scorer (or validation-only when no golden) |
| `official_score.py` | 354 | **verbatim copy** of the organisers' scorer. Do not edit. |

---

## 4. Architecture: how a submission is produced

```
                  ┌──────────────────────────── bankrec run --phase X ────────────────────────────┐
                  │                                                                                │
 data/valtermia ─►│ data.load()                                                                    │
                  │   bank lines (txns) · items (ARI/ARC/API/APC with OPEN amount at phase start)  │
                  │   masters · batch files (this phase + 4 months of the previous phase)          │
                  │                                                                                │
                  │ advices.load_advices()  ──► parsed remittance advices (cached in cache/)       │
                  │ NonItemModel.load()      ──► cache/nonitem.json                                │
                  │                                                                                │
                  │ Engine(data, model, advices).run()                                             │
                  │   1. stage_batches  : DD batches, card settlements, cheque deposits,           │
                  │                       payment runs, confirming        (exact file joins)       │
                  │   2. for each remaining line, in booking-date order → match_line():            │
                  │        return → advice → no-item (confident) → refs → party →                 │
                  │        credit refund → duplicate → no-item fallback / UNIDENTIFIED             │
                  │      (open balances are updated after every line: the "book")                  │
                  │                                                                                │
                  │ submission.write()  ──► submissions/<file>.csv.gz                              │
                  │ submission.score()  ──► score if golden exists, else validation only           │
                  │ append line to bankrec_scoreboard.md                                           │
                  └────────────────────────────────────────────────────────────────────────────────┘
```

### Key design decisions (and why)

1. **Rules + small learned model, no LLM in the loop.** About 200k lines must be decided in about 20 minutes, deterministically,
   with exact amounts. Rules follow POLICIES.md literally; the only learned piece is the no-item category table.
   (The Gemini/Claude agent layer lives in the old `closeops` project and is not used here.)
2. **Exact evidence first, fuzzy evidence last.** Batch files and quoted document numbers are near-certain, so they run
   first and remove items from the open book; name/amount guesses run later on what is left.
3. **A running open-item book.** `open` = amount still owed. Every allocation reduces it; `RETURNED` re-opens it.
   Lines are processed chronologically, so a later line never takes an invoice an earlier line already paid.
4. **Phase-to-phase continuity.** LIVE/FINAL ship `open_items_at_start`. We enrich those items with names/terms/PO
   from earlier phases, and we add items **already paid** in the last 6 months with `open = 0`, so a second payment of
   them can be flagged `DUPLICATE_PAYMENT`.
5. **Folders found by content, everything cached.** Extraction layouts differ between machines; parsing 16k PDFs is
   slow, so `cache/advices_*.json` makes a re-run fast.
6. **The official scorer is vendored** (`official_score.py`), so every local score is exactly what the organisers
   compute. On LIVE/FINAL (no golden) it still runs the **official validation**.
7. **Sample month = dev benchmark.** The sample has no opening balances, so `data._sample_opening_balances` rebuilds
   them from TRAIN's golden. This makes sample scores behave like LIVE.

### Data model inside the engine

`Engine.__init__` turns the items DataFrame into plain Python lists for speed (`self.ids`, `self.kind`,
`self.party`, `self.open`, `self.total`, `self.doc`, …), indexed by an integer `k`. Lookups:

- `self.pos[item_id] → k`
- `self.by_party[party_id] → [k…]`
- `self.docidx` (document number → k)
- `self.dd_by_mandate` (SEPA mandate → collections), `self.acct_entity` (bank account → entity)

Results: `self.result[bank_txn_id] = [Row(item, amount, category, exception), …]` and `self.how[bank_txn_id] = stage name`.
The final `by stage {...}` print counts `self.how`.

---

## 5. The engine stage by stage

All in `src/bankrec/engine.py`. Line numbers are approximate; search for the method name.

### Stage 1: batch files (`stage_batches`, `stage_confirming`)

| source file | bank line looks like | how we join | output |
|---|---|---|---|
| `direct_debit_batches/` | one credit = sum of a SEPA collection batch | `batch_total` + bank account + date (±7 days); fallback via mandate / name / ref suffix | one ARI row per collection |
| `card_settlements/` | one credit = card acquirer payout | sum of `net` per settlement | ARI rows (gross − fee handled per policy) |
| `cheque_deposits/` | one credit = cheque deposit | `deposit_total` | ARI row per cheque |
| `payment_runs/` | debit(s) = our supplier payment run | per end-to-end id or per run total | API rows; PARTIAL/FX on document rows |
| confirming runs | one debit settling N suppliers | exact subset of N beneficiaries (`_subset_count`) | API rows |

`lookup_doc()` maps the document reference in a batch file to an item (by number, entity, side, amount).

### Stage 2: per-line matching (`match_line`, in this order)

1. **`try_return`**: a returned direct debit or cheque. Finds the original collection through `dd_by_mandate`, re-opens the item,
   emits `RETURNED`.
2. **`try_advice`**: the line matches a **remittance advice** (by reference, truncated ref + amount, or payer +
   amount + date). `advice_rows` turns the advice's per-invoice lines into rows, including disputes, partials,
   discounts and overpayments. FX advices use `orig_amount`.
3. **Confident no-item.** If the learned model is ≥ 90 % sure (≥ 20 examples) the line is a fee/salary/tax/etc., stop here.
4. **References (`ref_items`)**: document numbers found in the bank text (`bank_blob` = all text fields, folded) →
   candidate items → `resolve_for` decides the allocation.
5. **Party (`rank_parties`)**: who paid or was paid. Matched by IBAN, then by IDF-weighted name words (prefixes allow
   truncated names), coverage ≥ 0.6, tie-breaks for exact name and word order. Then `party_refs` (ref suffix / prefix / PO match), then
   `resolve_for` over that party's open items.
6. **`try_credit_refund`**: refunds of credit notes (APC refund in, ARC payout out).
7. **`try_duplicate`**: the amount equals an invoice **already fully paid** → `DUPLICATE_PAYMENT`.
8. **Fallback (`no_item`)**: best category from the model. A receipt from a **known payer** we could not match gets
   category `UNIDENTIFIED` with no exception; a receipt from nobody we know gets `UNIDENTIFIED / UNIDENTIFIED`.

### `resolve_for`: choosing *which* open items a payment settles

Given candidate items, try in order, and stop at the first success:

1. the quoted items exactly
2. a subset of the quoted items
3. quoted items plus credit notes
4. `_gap_fill`: quoted plus one more item that fills the gap
5. a forced single item
6. `_settle_sub`: a subset over all candidates
7. `settle_fx`: currency conversion within 3 %
8. `_explained_single`: one item where the difference is an allowed short
9. `_withholding_multi`: several invoices with withholding tax
10. `_overpaid_single`: one item with a "signature" overpayment (round amount, exactly 1 % or exactly 10 %)

**`subset()`** finds which open items add up to the amount:

1. A single exact match first; with several same-amount invoices, prefer the one for the service month named in the text, else FIFO (oldest first).
2. Then a prefix by issue date, then a prefix by due date.
3. Then a dynamic-programming subset search, capped at 60k states (`MAX_SUBSET_ITEMS = 40`).
4. Then `_complement` (all candidates except a few).

**`open_of`** never offers an item issued more than 3 days after the booking date.

### `settle()`: turning a chosen set into rows and exceptions

Compares the paid amount with the open amounts and applies POLICIES (§6 below). It also caps overpayments at
`max(10 %, 50)`.

---

## 6. Exceptions: how each one is decided

| exception | rule we implement | row amount |
|---|---|---|
| *(none)* | paid = open (within tolerance) | open |
| `PARTIAL` | paid < open and no allowed short reason; item stays open | **paid** |
| `SHORT_DISCOUNT` | short = early-payment discount and booking ≤ issue + discount_days + **3 grace days** | open (fully cleared) |
| `SHORT_BANK_CHARGES` | short ≤ **€60** and ≤ **2 %** | open |
| `SHORT_WITHHOLDING` | customer in **MX / BR / MA**, short = 1.25 %, 1.5 % or 10 % | open |
| `SHORT_DISPUTE` | advice says disputed / quality claim | **paid** |
| `OVERPAYMENT` | paid > open, with a "signature" (round, 1 %, 10 %) | open |
| `DUPLICATE_PAYMENT` | the invoice was already settled | paid |
| `FX_DIFFERENCE` | paid in another currency, within 3 % after conversion; flagged on **every** invoice row | open |
| `RETURNED` | returned debit or cheque re-opens the item | returned amount |
| `UNIDENTIFIED` | receipt with no identifiable payer | no-item row |

Thresholds are constants at the top of `engine.py` (`FX_TOL`, `WITHHOLDING_RATES`, `WITHHOLDING_COUNTRIES`,
`OVERPAY_ROUND`, `MAX_SUBSET_ITEMS`). `short_reason()` picks which SHORT_* applies.

---

## 7. Commands reference

All commands run from the repo root. Default `--data` is `data/valtermia`.

```bash
# train the no-item category model from TRAIN golden  (writes data/valtermia/cache/nonitem.json)
.venv/Scripts/bankrec train-nonitem
.venv/Scripts/bankrec train-nonitem --exclude-sample        # honest sample scores (holds the sample month out)

# run a phase: writes the CSV, scores it (sample/train) or validates it (live/final), logs to bankrec_scoreboard.md
.venv/Scripts/bankrec run --phase sample --out submissions/sample_v22.csv.gz --note "v22 what changed"
.venv/Scripts/bankrec run --phase live   --out submissions/live_v5.csv.gz   --note "LIVE v5 = sample v22"
.venv/Scripts/bankrec run --phase final  --out submissions/final_v1.csv.gz  --note "FINAL"

# re-score / re-validate an existing file
.venv/Scripts/bankrec score submissions/sample_v21.csv.gz --phase sample
.venv/Scripts/bankrec score submissions/final_v1.csv.gz   --phase final     # validation only

# error analysis on the sample (needs the golden)
.venv/Scripts/python scripts/bankrec_diff.py    submissions/sample_v21.csv.gz ar_n_to_m 20   # wrong lines of one segment
.venv/Scripts/python scripts/bankrec_exc.py     submissions/sample_v21.csv.gz                # exception confusion
.venv/Scripts/python scripts/bankrec_missing.py submissions/sample_v21.csv.gz                # golden pairs we missed

# naive fallback (organisers' starter, stdlib only, score ≈ 19). Emergency only.
.venv/Scripts/python data/valtermia/docs/starter/starter.py --data data/valtermia/final --phase final --out submissions/final_starter.csv.gz
```

### Reading the run output

```
submissions/live_v4.csv.gz  load 37s match 1128s  lines 203083  by stage {'dd_batch': 1590, 'card': 1265, ...}
{ "ok": true, "score": ..., "metrics": {...} }
  ar_1to1_exact     n=  4120 acc=0.991        ← per-segment accuracy (sample/train only)
```

- `"ok": true` means the file passed the official validation. **Never upload a file without it.**
- `by stage`: how many lines each stage resolved. A big change from one version to the next (for example `unidentified` jumping) is
  a red flag.
- On LIVE/FINAL the score shows as `20.0` with F1 0 because there is no golden. That number is meaningless; only `ok` matters.

### Runtimes (this laptop)

| step | sample | LIVE (203k lines) | FINAL (≈ 220k lines) |
|---|---|---|---|
| load + advices (cached) | ~20 s | ~35 s | **first time: several minutes** (new PDFs to parse), then ~40 s |
| matching | ~3 min | ~19 min | **expect ~20–25 min** |

---

## 8. How to improve the score safely (dev loop)

```
 pick weakest segment ─► bankrec_diff.py shows wrong lines ─► read POLICIES.md for that case
        ▲                                                          │
        │                                                          ▼
 commit + log  ◄── sample score went UP? ◄── run --phase sample ◄── change engine.py (generic rule)
        │                      │ no → git checkout src/bankrec/engine.py (revert)
        ▼
 run --phase live → upload → compare LIVE score (the real test)
```

Rules for this loop:

1. **One idea per version.** Name it `sample_vNN` with a `--note`, so the scoreboard tells the story.
2. **Generic rules only.** Never key logic on a specific id, customer or amount from the data. That overfits the
   sample and breaks on FINAL, and the CLAUDE.md rules forbid it.
3. **Sample up isn't enough.** We tuned on the sample month, so check LIVE too: upload and compare. If LIVE drops
   while the sample rises, revert.
4. **Commit after every improvement:**
   ```bash
   git add src/bankrec bankrec_scoreboard.md
   git commit -m "feat(bankrec): <what> (sample NN.NN)"
   git push
   ```
5. The `pytest` suite (≈ 300 tests) covers the old `closeops` project. **bankrec has no unit tests**: its safety net is
   the official scorer on the sample plus official validation on LIVE/FINAL. Run `.venv/Scripts/python -m pytest -q` before
   committing anyway, so nothing else breaks.

### Score history (sample month, official scorer)

| version | score | change |
|---|---|---|
| starter | 11.28 | organisers' naive baseline |
| v1 | 58.79 | batches, refs, party matching, no-item model |
| v6 | 73.82 | remittance advices |
| v10 | 84.03 | returns, credit notes, subset search |
| v17 | 88.71 | same-name party tie-breaks |
| v20 | 90.78 | overpayment signature, multi-invoice withholding |
| **v21** | **91.38** | duplicate payments of already-settled invoices |

---

## 9. Where we are now and what is still weak

**Status at the end of Friday 2026-11-06**

- Engine v21: **91.38** on the sample. `submissions/live_v4.csv.gz` is the LIVE file, validated (`ok: true`), 6.3 MB.
- **No LIVE score yet.** The public leaderboard was empty. Uploading `live_v4` is task #1 tomorrow.
- Code pushed to `github.com/Pratyush2586/CuentaMinds` (private).

**Known weak spots** (sample F1 / accuracy). These are the improvement candidates, in priority order:

| # | weakness | why it matters | idea |
|---|---|---|---|
| 1 | `SHORT_DISPUTE` F1 ≈ 0.72, `PARTIAL` ≈ 0.75 | exception macro-F1 is 20 % of the score; each rare exception weighs the same | read dispute/partial wording in advices and PDFs; amount = paid |
| 2 | `UNIDENTIFIED` F1 ≈ 0.73 | same | sharpen "known payer" vs. unknown |
| 3 | `ar_n_to_m` ≈ 0.36, `ar_1toN_credit_note` ≈ 0.68 | many pairs per line, so they weigh heavily in pair F1 | better subset search with credit-note netting |
| 4 | PDF clauses unused: instalments, retention, advance deduction, "anula y sustituye" (cancel & replace), factoring | POLICIES §5 says these only appear in PDFs | parse `pdfs/` index + text, map clause → expected split |
| 5 | `ar_discount_late` low | discount window edge cases | re-check grace-day boundary |

**FINAL drift to watch** (POLICIES.md §6):

- **New subsidiary ZA01** from 2026-01-01: new bank, currency **ZAR**, invoice numbers "KWI…", renamed on 2026-04-01.
  Updated masters come in `final/masters_final/`; `data.find_masters` already prefers them in FINAL.
  Checked on Friday: ZAR is already in the current `fx_rates.csv`, but those rates stop at **2025-12-31**. If
  `masters_final` isn't picked up, `Engine.rate()` reuses the last 2025 rate for every 2026 date. That doesn't crash, but
  FX matches get worse. Step 3 of §11 prints "masters from …" and "fx up to …" so you can confirm (fx should reach 2026-06).
- Banco del Moncayo statements are camt.053 since 2025-10-01 (already the case in LIVE).
- Customers renamed or changing bank; slower payments in 2026. Party matching by IBAN + name handles some of this.

---

## 10. Hackathon day timeline (tomorrow)

All times **Madrid (CET)**. Server deadlines from the site:

- FINAL release **18:00**
- **Submissions close 19:30**
- **Projects close 19:45**
- Presentations 19:45
- Reveal 20:30

| time | who | action |
|---|---|---|
| 09:00 | all | check-in. `git pull`; run the §2 smoke test (sample ≈ 91.4) |
| 09:30 | all | briefing. **Listen for any rule change** (FINAL format, deadlines) |
| 10:00 | Pratyush | LIVE opens. Check the team status is **APPROVED** on the site. **Upload `submissions/live_v4.csv.gz`** on the Enviar (Submit) page |
| 10:05 | dev | copy the result page's **per-segment accuracy** into `docs/live_results.md`; compare with the sample (§9). Segments far below the sample are the first fixes |
| 10:15–15:30 | dev | improvement loop (§8) on the weak spots. Every ~1–2 h: run LIVE with the best engine (~20 min, run it in the background) and upload. Keep the version with the **best LIVE score** |
| 14:00 | all | lunch. Leave a LIVE run going |
| **15:30** | dev | **CODE FREEZE** for risky changes. Tag the best engine: `git tag final-engine && git push --tags` |
| 15:30–17:30 | presenter | record the **video** (§12), write the **write-up**, prepare the 19:45 presentation |
| 17:30 | dev | last LIVE validation of the tagged engine; check `git status` is clean; laptop on power; close other heavy apps |
| **18:00** | dev | **FINAL released → follow §11 exactly** |
| ~18:45 | Pratyush | upload the validated FINAL file (**once**) |
| 19:00 | Pratyush | make the repo public / share access, fill the Project page, **Save** |
| 19:30 | | submissions closed |
| 19:45 | all | projects closed; team presentations |
| 20:30 | all | reveal |

---

## 11. FINAL runbook (18:00 → 19:30)

> Goal: one **valid** FINAL file uploaded by **~18:45**, leaving 45 minutes of buffer.
> Do not change engine code during this window unless the run crashes.

### Step 1: download and verify (18:00–18:05)

On the site's **Datos** (Data) page, download **FINAL (2026-01 → 2026-06)** into `data/`. Then compare its SHA-256 with the one on
the page:

```bash
cd /c/Users/learn/Documents/prat/Hackathon/nova7nov
sha256sum data/bankrec-final.tar.gz              # must equal the sha256 shown on the Datos page
```

### Step 2: extract (18:05–18:08)

```bash
mkdir -p data/valtermia/final
tar -xzf data/bankrec-final.tar.gz -C data/valtermia/final
find data/valtermia/final -maxdepth 3 -name "bank_transactions.csv.gz"   # e.g. data/valtermia/final/final/bank_transactions.csv.gz
find data/valtermia/final -maxdepth 4 -type d -name "masters_final"      # updated masters must be found
```

If the archive extracts under a different name, it doesn't matter: the loader searches for a folder named `final`
containing `bank_transactions.csv.gz`.

### Step 3: quick sanity check of the new data (18:08–18:10)

```bash
.venv/Scripts/python - <<'EOF'
from bankrec import data as D
d = D.load("data/valtermia", "final")
print("lines", len(d.txns), "dates", d.txns.booking_date.min(), d.txns.booking_date.max())
print("masters from", d.masters_dir)
print("currencies", sorted(d.txns.currency.unique()))
print("entities", sorted(d.entities.entity.unique()))
print("ZAR in fx", "ZAR" in set(d.fx.currency), "| fx up to", d.fx.date.max())
print("open items", int((d.items.open > 0).sum()), "| batch rows dd/cards/cheques/payruns",
      len(d.dd), len(d.cards), len(d.cheques), len(d.payruns))
EOF
```

Expect about 220k lines from 2026-01-01 to 2026-06-30, masters from `.../masters_final`, ZA01 among the entities, and ZAR in fx.

### Step 4: run (18:10 → ~18:40)

```bash
.venv/Scripts/bankrec run --phase final --out submissions/final_v1.csv.gz --note "FINAL = engine $(git rev-parse --short HEAD)"
```

- The first run parses the new PDFs and advices, which takes a few minutes before matching starts. Don't kill it because it looks quiet.
- PowerShell users: run it as one line; the `$(...)` note part also works in PowerShell 7. Or just type a note.

### Step 5: check before uploading (≈ 18:40)

The run must print `"ok": true`. Then sanity-check the stage counts against LIVE (`live_v4`):

| stage | LIVE v4 | FINAL should be roughly |
|---|---|---|
| lines | 203,083 | ≈ 220,000 |
| ref + party | 98k | similar share (≈ 45–50 %) |
| payrun | 53,670 | similar share |
| unidentified | 2,571 | low thousands. **Tens of thousands means something is broken** (masters/items not loaded) |

```bash
.venv/Scripts/bankrec score submissions/final_v1.csv.gz --phase final       # re-validate: "ok": true
ls -la submissions/final_v1.csv.gz                                         # size < 60 MB
```

### Step 6: upload once (≈ 18:45)

Pratyush uploads `submissions/final_v1.csv.gz` on the **Enviar** (Submit) page in phase FINAL. If the site *rejects* it in validation,
read the error, fix it, and re-upload (rejected files don't count). **Once a file is accepted, stop.**
Then commit the log:

```bash
git add bankrec_scoreboard.md && git commit -m "chore(bankrec): FINAL submission logged" && git push
```

### If something goes wrong

| symptom | action |
|---|---|
| crash with a Python traceback | read the last line. Most likely a new value (ZA01/ZAR, new column). Patch it with a guard (`.get`, `try/except` that falls back), re-run. If not fixed by **19:05**, use the fallback below |
| run still matching at 19:00 | let it finish (it's typically ≤ 25 min). In parallel, generate the fallback file |
| `"ok": false` | the errors are printed (missing lines, bad category, bad amount). Fix and re-run `score` |
| **19:10 and no valid file** | **fallback**: `.venv/Scripts/python data/valtermia/docs/starter/starter.py --data data/valtermia/final --phase final --out submissions/final_starter.csv.gz`, validate it with `bankrec score`, upload. Scores ≈ 19, but it's better than nothing |

---

## 12. Project hand-in (video, repo, write-up)

On the site's **Proyecto** (Project) page, before **19:45**. Only the organisers and the jury see it. The jury judges **the approach**
as well as the CSV score.

### Checklist

- [ ] **Video**: about 5 min at 720p, as .mp4, .mov or .webm (≤ 1 GB), or an unlisted YouTube / Loom / Drive link that opens without signing in
- [ ] **Repository**: `https://github.com/Pratyush2586/CuentaMinds`. Make it accessible:
      `"C:\Program Files\GitHub CLI\gh.exe" repo edit Pratyush2586/CuentaMinds --visibility public --accept-visibility-change-consequences`
- [ ] Tick **"Repo access confirmed"**
- [ ] **Write-up**: key ideas, stack and models, what we'd do next, team members (template below)
- [ ] **Save** (and check "Saved …" appears)
- [ ] FINAL submission made (§11)

### Write-up template (paste, then edit the numbers)

```
CuentaMinds: deterministic, policy-faithful bank reconciliation for Grupo Valtermia

Key ideas
- Evidence ladder: exact batch-file joins (SEPA direct-debit batches, card settlements, cheque deposits,
  payment runs, confirming) → returns → remittance advices (PDF/XLSX/CSV/TXT parsed) → document references
  in bank text → payer identification (IBAN + IDF-weighted names) → learned no-item categories.
- Running open-item book processed chronologically, carried across phases (open_items_at_start + recently
  settled items, to catch duplicate payments).
- Exact subset-sum search (FIFO / service-month aware, capped DP) to decide which invoices a payment covers.
- POLICIES.md implemented literally: discount window + grace, bank-charge thresholds, MX/BR/MA withholding,
  3% FX tolerance, partial vs dispute amounts, returns re-opening items.
- The organisers' scorer is vendored; every change is measured on the sample month and validated on LIVE.

Results
- Sample month: 11.3 (starter) → 91.38. LIVE: <fill in>. 

Stack and models
- Python 3.12, pandas, pdfplumber, openpyxl. No LLM in the scoring path (speed, determinism, exact amounts).
- Built with Claude Code (pair-programming the engine, analysis scripts, docs).

What we would do next
- Parse PDF contract clauses (instalments, retentions, advances, cancel-and-replace, factoring).
- Better many-to-many / credit-note netting; held-out TRAIN months for generalisation.

Team: CuentaMinds: <names>
```

### Video outline (5 min)

1. 0:00–0:30. The problem: 200k bank lines, 4 item types, 10 exceptions, and the scoring formula.
2. 0:30–2:00. Architecture diagram (§4) and the evidence ladder.
3. 2:00–3:00. Live demo: `bankrec run --phase sample`, the score printout, the per-segment accuracy.
4. 3:00–4:00. The score history table (§8) and the LIVE result.
5. 4:00–5:00. Drift handling (ZA01/ZAR, camt switch), what's next, the team.

---

## 13. Troubleshooting

| problem | fix |
|---|---|
| `bankrec: command not found` | use `.venv/Scripts/bankrec` (or `.venv/Scripts/python -m bankrec.cli`) |
| `FileNotFoundError: no final/bank_transactions.csv.gz` | the FINAL archive isn't extracted under `data/` (§11 step 2) |
| `UnicodeEncodeError` in the console | Windows console encoding; the CLI already forces UTF-8. For your own scripts, add `sys.stdout.reconfigure(encoding="utf-8")` |
| run is very slow (> 40 min) | the laptop is on battery or another heavy process is running; the PDF parse isn't cached yet (first run only) |
| memory error | close the browser and other apps; the engine needs a few GB |
| `git push` asks for a password / fails | `"C:\Program Files\GitHub CLI\gh.exe" auth setup-git`; log in with `gh auth login --web` as Pratyush2586 |
| sample score dropped after a change | `git diff src/bankrec`; revert with `git checkout src/bankrec/engine.py` |
| site says 429 / "wait" | 3 minutes minimum between uploads, 20 per day per phase |

---

## 14. Rules we must never break

1. **Never share or commit the team token**, API keys (`.env`, `claudeapi.txt`) or `data/`. Only Pratyush uploads.
2. **Fair play:** no attempts to obtain the LIVE/FINAL golden, no probing or attacking the platform. Every
   improvement must come from the data and POLICIES.md.
3. **No logic keyed to specific ids, customers or amounts.** Generic rules only.
4. **Never upload a file that didn't print `"ok": true` locally.**
5. **FINAL: upload once.** A validated file, by ~18:45.
6. Log every run (the CLI does it in `bankrec_scoreboard.md`) and commit after every improvement.
