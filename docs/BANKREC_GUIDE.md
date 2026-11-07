# CuentaMinds — Bank Reconciliation Engine: Developer Guide

> **Who this is for:** a developer joining the team who must understand, run, evaluate, improve and submit this code.
> **State as of:** 2026-11-07 17:15 · `main` = FINAL engine candidate (engine 0f25a3e: TRAIN eval 94.105, hold-out 2025-05 93.18) · step-by-step PowerShell commands: [`docs/RUNBOOK_POWERSHELL.md`](RUNBOOK_POWERSHELL.md).
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
| `README.md` | project front page (results, how it works, quick start) |
| `docs/RUNBOOK_POWERSHELL.md` | **hands-on runbook**: every PowerShell command (setup, data, run, evaluate, inspect, submit, FINAL) |
| `docs/SME_REVIEW_PACK.md` | data and questions shared with the team's finance/banking SME (local, not committed) |
| `src/bankrec/` | **the reconciliation engine** (below) |
| `tests/bankrec/` | bankrec unit tests (FINAL masters merge) |
| `bankrec_scoreboard.md` | every `bankrec run` and every platform upload appends a line here |
| `bankrec_calibration.tsv` | TRAIN-eval vs LIVE-score pairs per engine commit (§8.4) |
| `docs/BANKREC_GUIDE.md` | this guide |
| `docs/AUTORESEARCH.md` | the experiment protocol the agent follows ("program.md") |
| `docs/GETTING_STARTED.md`, `USER_GUIDE.md`, `HACKATHON_PLAYBOOK.md` | closeops docs (background) |

**Scripts**, grouped by purpose:

| group | script | what it does |
|---|---|---|
| analysis | `scripts/bankrec_pairs.py SUB [--month M]` | where (bank line, item) pairs are lost, per golden segment |
| analysis | `scripts/bankrec_exc.py SUB [--month M]` | per-exception precision / recall / F1 |
| analysis | `scripts/bankrec_diff.py SUB SEGMENT N [--month M]` | the wrong lines of one segment: bank text, golden-only rows, ours-only rows |
| analysis | `scripts/bankrec_probe.py MONTH TXN...` | engine internals for given lines (candidates, ranked parties, refs) |
| evaluation | `scripts/autoresearch.py baseline/eval/holdout/status` | **experiment harness** (§8.3). Read-only during the loop |
| evaluation | `scripts/overfit_guard.py [BASE] [HEAD]` | static scan of engine diffs for memorised ids/dates/amounts/codes (exit 1 on findings) |
| evaluation | `scripts/live_report.py [NEW] [OLD]` | **LIVE feedback**: platform result vs previous upload vs TRAIN months, ranked by lines lost |
| evaluation | `scripts/final_gate.py calibrate / check PHASE FILE` | **FINAL readiness gate**: label-free profile, calibrated on scored LIVE files → PASS/WARN/FAIL |
| agents (LLM) | `scripts/analyst.py hypotheses / skeptic COMMIT / final-review GATE.txt` | **Claude personas**, advisory (§8.5) |
| submission | `scripts/submit.py LIVE\|FINAL FILE` | **upload pipeline**: the organisers' 4-step API with guards (§8.6) |
| submission | `scripts/pipeline.py --tag vN [--submit] [--yes]` | **end-to-end LIVE**: build → validate → **checkpoint (y/N)** → upload + score → report |
| submission | `scripts/checkpoint.py live\|final FILE` | **upload checkpoint**: internal assessment (predicted score, gate) + your decision |
| analysis | `scripts/pdf_clauses.py`, `scripts/pdf_keywords.py` | mine the clause templates of the document PDFs |
| submission | `scripts/final_run.py [--download] [--upload --i-confirm-final-is-one-shot]` | **FINAL orchestrator** (§11) |

### `src/bankrec/` file by file

| file | lines | responsibility |
|---|---|---|
| `cli.py` | 166 | `bankrec train-nonitem / run / score`. Pins `PYTHONHASHSEED=0` (re-executes itself) so runs are reproducible |
| `data.py` | 297 | loads a phase (`load`) or a **TRAIN month** (`load_train_month`): bank lines, item book with open amounts, masters, batch files |
| `engine.py` | 1370 | **the matcher**: stages, subset search, party identification, exception rules |
| `text.py` | 85 | `fold`, `norm`, `digits`, `tokens`, `bank_blob`, `name_key`, `DocIndex` (document-number lookup), `LEGAL` |
| `advices.py` | 144 | parses remittance advices (pdf/xlsx/csv/txt) into refs, total and per-invoice lines. Parallel and cached |
| `nonitem.py` | 77 | learned table: (sign, bank code, text shape) → category, trained on TRAIN golden |
| `submission.py` | 47 | writes the CSV; scores with the official scorer, or validates only when there's no golden |
| `pdfdocs.py` | ~140 | **PDF facts**: instalments, retention payable, advance deduction, cancel-and-replace, cancelling / paid-out credit notes (§5) |
| `src/bankrec/official_score.py` | **verbatim** organisers' scorer. Never edit |

---

## 4. Architecture: how a submission is produced

```
 bankrec run --phase X  (or --month YYYY-MM for a TRAIN-month evaluation)
   │
   ├─ data.load / data.load_train_month
   │     bank lines · item book (ARI/ARC/API/APC, OPEN amount at period start) · masters
   │     (FINAL: masters_final merged with the previous masters so old names/IBANs survive)
   │     batch files of the period + 4 months before
   ├─ advices.load_advices  → parsed remittance advices (cache/advices_*.json)
   ├─ NonItemModel.load     → cache/nonitem.json (or nonitem_ex_<month>.json for an evaluation)
   │
   ├─ Engine(data, model, advices).run()
   │     1. stage_batches: exact joins with batch files
   │          direct debits · card settlements · cheque/lockbox deposits (short stub numbers resolved) ·
   │          payment runs (amount, else quoted e2e/run id) · confirming (beneficiary maturity)
   │     2. lines sorted by booking date; within a day, lines quoting our document numbers first
   │        match_line(): return → reversal/advance → advice → confident no-item → refs → party →
   │                      credit refund → supplier duplicate refund → duplicate
   │        (every allocation updates the open-item book; RETURNED re-opens)
   │     3. no_item() for whatever is left: category, or UNIDENTIFIED
   │
   ├─ submission.write   → submissions/<file>.csv.gz   (+ <file>.stats.json run internals for the gate)
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

1. `try_return`: a returned direct debit re-opens the original collection → `RETURNED`.
2. `try_reversal_or_advance` handles flows POLICIES names that had no stage before 2026-11-07:
   - **receipt quoting a proforma** (`PROFORMA`, `PF..-....`) and **no invoice reference** → category `CUSTOMER_ADVANCE`;
   - **returned supplier transfer**: an incoming "RETURN / DEVOLUCION / RETOUR / STORNO…" that quotes one of our
     payment-run end-to-end ids → that run's documents, `RETURNED`, re-opened. The amounts must add up. Payment-factory
     entities (ES00 → ES01/ES02/PT01) are allowed;
   - **returned cheque / card chargeback** debit → the invoice behind a cheque number or card order **present in the batch
     files**, `RETURNED`, re-opened.
3. `try_advice`: a matching **remittance advice** (by ref, truncated ref + amount, or payer + amount + date).
   `advice_rows` turns its lines into rows (§6). `_adv_doc` resolves each advice line in this order:
   1. our document number;
   2. a **short number** that is a suffix of the payer's own receivables;
   3. the **customer PO**;
   4. a short number plus the same gross amount anywhere in the entity.

   Steps 2–4 cover the Sept-2025 layout drift (§9.5).
4. **Confident no-item:** the model is ≥ 90 % sure (≥ 20 examples) → category.
5. **References:** `ref_items` → document numbers in the text → `resolve_for` over that payer family.
   An already-paid quoted invoice of the same amount → `DUPLICATE_PAYMENT`.
6. **Party:** `rank_parties`:
   - IBAN first;
   - then IDF-weighted name words (prefixes for truncated words), coverage ≥ 0.6;
   - coverage **rounded to 6 decimals**, so a full match of 0.9999999999 ties with 1.0;
   - ties broken by exact full name and word order.

   Then `resolve_for` per party.
7. `try_credit_refund`: credit-note refunds (APC refund in, ARC payout out).
8. **Duplicate-payment refund**: a receipt with refund/duplicate wording, no invoice reference, from an identified
   **supplier** → category `SUPPLIER_REFUND`.
9. `try_duplicate`: the amount equals an invoice already paid → `DUPLICATE_PAYMENT`.
10. `no_item`: the model's category. A receipt from a **known payer** gets category `UNIDENTIFIED` with no exception;
    a receipt nobody can be tied to gets `UNIDENTIFIED / UNIDENTIFIED`.

### `resolve_for`: which open items a payment settles

Candidates = `open_of(family)`: open items on the right side, issued ≤ booking + 3 days. It tries in order and stops at
the first success.

**With quoted items:**
1. the quoted items exactly
2. a subset of them
3. quoted + credit notes
4. a subset of quoted + credit notes
5. `_gap_fill` (quoted plus a subset of the payer's other open items for the gap)
6. **`_one_partial`**: the quoted invoices are paid in full except ONE, paid by a round share of its total (`PARTIAL_FRACTIONS`); all items must be in the line's currency
7. a forced single item, when only one invoice is quoted

**Then, for all candidates:**
1. `_settle_sub`
2. `settle_fx` (3 % tolerance)
3. `_explained_single` (one item with an allowed short)
4. `_withholding_multi`
5. `_overpaid_single` (round, 1 % or 10 % overpayment)
6. **`_instalment`**: a part payment of ONE invoice → `PARTIAL` for the paid amount. With a `k/N` marker ("INSTALMENT 1/3", "PRESTACAO 2/6", "ECHEANCE 1/6") the cash must be total/N; without one, total × f for f in {1/2, 1/3, 1/4, 1/6, 1/12} ∪ `PARTIAL_FRACTIONS`, accepted only when exactly one open invoice matches.

`PARTIAL_FRACTIONS = (0.25, 0.3, 0.4, 0.5, 0.6, 0.7)` is **the distribution of paid/total in TRAIN golden partials**. It was
identical in 2025-03 and 2024-10, so it isn't fitted to LIVE.

### PDF facts (`pdfdocs.py`, used in `subset()`, `open_of()`, `ref_items()`)

The 131k document PDFs are generated **text**: no images, and no tables that need OCR, so `pdfplumber` plus
multilingual regexes is enough and no LLM is needed. They state facts that exist **nowhere else** (POLICIES §5.7–5.9):

| clause (examples) | fact | use in the engine |
|---|---|---|
| "Payment terms: 3 instalments at 60/90/120 days (6,919.30 / 6,919.30 / 6,919.31 USD)" · "3 vencimientos a 60/120/180 días (…)" | every instalment amount | a payment equal to one → that invoice, `PARTIAL` (the last one clears it) |
| "Retention 10% (Y) withheld until final acceptance … Amount payable at due date: Z" · "Retención en garantía del 5 % … Importe a pagar al vencimiento: Z" | the first payment = Z | 1:1 → `PARTIAL`; the later release of Y clears it |
| "Less advance received against proforma PF…: X" | payment = total − X | 1:1 → `PARTIAL` |
| "This invoice cancels and replaces invoice X" · "anula y sustituye a la factura X" | X is void | a payment quoting X settles the replacement; X is never a candidate |
| credit note "CANCELLING OF INVOICE Nº X" | both are void | neither is ever a candidate |
| rebate credit note "…will be paid to your bank account" vs "…to be offset against your next payment" | paid out vs netted | paid-out notes are never netted into receipts |

Sanity checks (skeptic review):
- an instalment plan must add up to the total;
- retention payable must be 85–97 % of the total (5–10 % retained);
- 0 < advance < total;
- facts are keyed by document type.

Parsed once per phase and cached in `cache/pdffacts_<phase>_v<PARSER_VERSION>.json`: TRAIN 268 s, LIVE 26 s. Any failure falls
back to *no facts* and never stops a run. **Effect: +0.33 on the TRAIN evaluation (91.225 → 91.555)**; the LIVE check is v14.

TRAIN counts with facts:

| fact | count |
|---|---|
| instalment plans | 11,400 |
| cancel-and-replace | 12,766 |
| retention payables | 7,281 |
| advance deductions | 1,811 |
| cancelling credit notes | 2,240 |
| paid-out credit notes | 11,205 |

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
| `PARTIAL` | paid < open, no allowed short reason, item stays open: includes a discount taken **after** its window, **instalments** (total/N) and round-share part payments | **paid** |
| `SHORT_DISCOUNT` | short = early-payment discount and booking ≤ issue + discount days + **3 grace days** | open |
| `SHORT_BANK_CHARGES` | short ≤ €60 and ≤ 2 %, foreign payment | open |
| `SHORT_WITHHOLDING` | customer in MX/BR/MA, short = 1.25 %, 1.5 % or 10 % | open |
| `SHORT_DISPUTE` | advice shows a deduction **the policy does not explain** | **paid** |
| `OVERPAYMENT` | paid > open, round / 1 % / 10 % signature | open |
| `DUPLICATE_PAYMENT` | the invoice was already settled | paid |
| `FX_DIFFERENCE` | invoice currency ≠ account currency, within 3 % after conversion; on **every** invoice row | open |
| `RETURNED` | returned direct debit, **returned supplier transfer**, **returned cheque**, **card chargeback**: re-opens the item | returned amount |
| `UNIDENTIFIED` | receipt nobody can be tied to | no-item row |
| category `CUSTOMER_ADVANCE` | receipt quoting a proforma, no invoice reference (no invoice exists yet) | no-item row |
| category `SUPPLIER_REFUND` | a supplier returning our duplicate payment (no credit note) | no-item row |

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

### 8.4 Evaluation strategy for a one-shot FINAL

FINAL gives **one** scored upload and no feedback until the reveal. So the decision to upload rests on evidence we
build ourselves, in four layers. Each one answers a different question and has a different tool:

| layer | question | tool | evidence |
|---|---|---|---|
| **1. TRAIN dev months** | is the logic right? | `autoresearch.py eval` (official scorer, real golden, held-out category model) | 2025-03 + 2024-10 mean; keep ≥ +0.02, no month −0.30 |
| **2. TRAIN hold-out** | does it generalise beyond the months we tune on? | `autoresearch.py holdout` (2025-05, never used for decisions) | must move in the same direction as the dev mean |
| **3. LIVE uploads** | does it carry over to unseen, drifted data? | `pipeline.py --submit` → `live_report.py` (per segment vs TRAIN) | platform score; segments failing *only* on LIVE = drift |
| **4. FINAL gate** | did anything break on the FINAL inputs? | `final_run.py` → `final_gate.py` (label-free) → `analyst.py final-review` | PASS / WARN / FAIL vs the best scored LIVE file |

#### Calibration: TRAIN eval → LIVE (`bankrec_calibration.tsv`)

Every LIVE score is paired with the TRAIN-eval score of the same engine commit:

| LIVE file | TRAIN eval | LIVE | gap |
|---|---|---|---|
| live_v7 | 90.27 | 87.56 | 2.71 |
| live_v8 | 90.55 | 87.90 | 2.65 |
| live_v9 (drift fix) | 90.55 | 90.32 | **0.23** |
| live_v10 | 90.77 | 90.69 | **0.08** |
| live_v11 | 90.88 | 90.81 | **0.07** |
| live_v12 | 91.16 | 91.11 | **0.05** |

The gap was almost entirely the Sept-2025 advice-layout drift (a LIVE-only behaviour). After that fix, **the TRAIN
evaluation predicts LIVE within about 0.2**. The harness prints `predicted LIVE ~ eval − median recent gap` for every
experiment. **Add a row after every LIVE upload.** If the gap opens again, a new drift exists: find it with
`live_report.py` (the gap column) before tuning anything else.

What the scoring rewards, learned from the uploads:
- **Pair recall** dominates. v9's +2.4 came from many-invoice advices (recall 0.834 → 0.874).
- **Exception macro-F1** is 20 % but each exception type weighs the same. Rare exceptions such as PARTIAL, SHORT_DISPUTE and DUPLICATE_PAYMENT pay off.
- **Line accuracy** moves least, because big lines are rare.

#### Anti-overfitting rules (all enforced or checked)

1. **Decide on TRAIN, confirm on the hold-out, observe on LIVE.** LIVE feedback *finds* drift; it never sets a number.
2. **Constants come from POLICIES.md or from distributions in TRAIN golden** that hold in more than one month (e.g.
   `PARTIAL_FRACTIONS` = 25/30/40/50/60/70 % seen identically in 2025-03 and 2024-10). Never fit a constant to LIVE.
3. **No literals keyed to the data.** `scripts/overfit_guard.py [BASE]` scans engine diffs for record/party ids, dates,
   money literals and entity/account codes. It exits 1 when it finds any, and it had 0 findings since last night.
4. **One change per experiment.** The harness keeps or reverts it. The simplicity rule applies: equal score with simpler code is kept.
5. **Skeptic review before promotion.** `analyst.py skeptic COMMIT` (Claude) checks overfitting, FINAL-drift and
   runtime risk. Its first review found a real currency-mixing bug and a runtime trap; both were fixed.
6. **Drift fixes** (LIVE/FINAL-only behaviour) are kept only when TRAIN is neutral **and** a direct measurement on the
   LIVE inputs justifies them, then confirmed by the next LIVE upload (`docs/AUTORESEARCH.md`).
7. **Code freeze at 15:30.** FINAL runs the exact engine of the best LIVE file. After 18:00 only a gate FAIL justifies a change.

#### Agents in the framework (Claude API, advisory only)

`scripts/analyst.py` uses `claude-opus-5-5` (adaptive thinking, effort high, refusal fallback). It never edits code and
never sees answers; every report is saved to `autoresearch/analyst/` with its cost (about $0.10 per call, budget $100).

| persona | input | output | when |
|---|---|---|---|
| `hypotheses` (analyst) | LIVE report, calibration, experiment log, POLICIES | the 5 next experiments, with mechanism, generic rule, TRAIN test and overfit risk | after each LIVE upload |
| `skeptic` (reviewer) | one experiment's diff + log | KEEP / KEEP WITH CHANGES / REVERT, with reasons | before promoting to `main` |
| `final-review` (release manager) | FINAL gate output + calibration | go / no-go, expected range, residual risks | tonight, step 6 of `final_run.py` |

The coding agent (Claude Code) runs the ReAct loop (§8.3). The personas are a second opinion that is cheap,
reproducible and logged.

### 8.5 LLM integration in detail (Claude API)

| item | value |
|---|---|
| where | `scripts/analyst.py` only. The engine, scorer and gate never call an LLM (determinism, exact amounts, speed) |
| model | `claude-opus-5-5` through the official `anthropic` Python SDK (2.5.0), adaptive thinking, `output_config.effort = "high"` |
| safety | server-side refusal fallback (`server-side-fallback-2026-07-01`, `fallbacks: "default"`), with a plain request if that beta is refused; `stop_reason == "refusal"` handled |
| key | `ANTHROPIC_API_KEY` from the environment or the git-ignored `.env` (synced from `claudeapi.txt`); never printed |
| prompt | fixed framework context + the full `POLICIES.md` as a cached system block, plus the persona's role; the user turn carries the reports |
| inputs | LIVE per-segment report, calibration table, autoresearch log, experiment diffs, gate output. **Never** answers for LIVE/FINAL (there are none) |
| outputs | markdown notes in `autoresearch/analyst/<timestamp>_<mode>.md` with token usage and cost |
| cost | $0.10–0.25 per call (Opus 5.5: $4 / $20 per 1M tokens). The budget is $100 |
| results so far | **skeptic** found a currency-mixing bug and a combinatorial runtime trap in `_one_partial`, and the misfire risks of the first proforma/refund/return rules (fixed in `f62fe80`). **analyst** ranked the unimplemented flows that became the +0.28 experiment |
| earlier work | the closeops prototype has a separate agent layer (`src/closeops/agents/`, Gemini default, Claude optional). It isn't used by bankrec |

### 8.6a Upload checkpoint (`scripts/checkpoint.py`)

Before **any** upload, the pipeline shows the internal assessment and waits for a human decision. This is what protects
the single FINAL submission:

```
========================================================================
  UPLOAD CHECKPOINT · FINAL · submissions/final_v1.csv.gz
========================================================================
  internal TRAIN-month evaluation (best kept)   : 91.56
  calibrated TRAIN->LIVE gap (recent uploads)  : +0.07
  predicted platform score                     : ~91.49
  best LIVE score so far                       : 91.11
  FINAL readiness gate                         : PASS
  expected FINAL range (unseen drift)          : ~89.5 .. 91.8
  REMINDER: FINAL = ONE scored submission. The first valid upload is the score.
========================================================================
Do you agree with this assessment and want to upload the FINAL file? Type UPLOAD to confirm:
```

| mode | question | answer that uploads | on no |
|---|---|---|---|
| LIVE | `[y/N]` | `y` | prints how to improve (§7–8 of the runbook) |
| FINAL | type `UPLOAD` | `UPLOAD` (or `OVERRIDE` on a gate FAIL) | nothing sent; return later with `final_run.py --skip-run --file …` |

Notes:
- In a non-interactive terminal it never uploads.
- `pipeline.py --yes` skips the question for unattended LIVE runs. FINAL can't be unattended.

### 8.6 Submission pipeline in detail

The organisers publish a 4-step API, and `scripts/submit.py` implements it with guards:

| step | call | guard |
|---|---|---|
| 0 | local official validation of the file against the phase's bank lines | must be `"ok": true`, or nothing is sent |
| 0 | platform history `GET /api/submissions` | LIVE: ≥ 180 s since our last upload, < 20 today. FINAL: refuses if a FINAL is already scored and needs `--i-confirm-final-is-one-shot` |
| 1 | `POST /api/submissions/upload-url {phase, filename}` | 201 expected; returns `sub_id` and a presigned S3 form |
| 2 | `POST <S3 url>` with the form fields, then the file | 200/201/204 |
| 3 | `POST /api/submissions/{sub_id}/score` | |
| 4 | `GET /api/submissions/{sub_id}` every **5 s** (the venue shares one IP), max 10 min | ends on scored / received / invalid / error |

- The result JSON (score, metrics, 60+ segment accuracies) is saved in `submissions/results/`, and a line goes to `bankrec_scoreboard.md`.
- The token is `HK_TOKEN`, from the environment or the git-ignored `.env`. It is never printed, logged or committed.
- `scripts/pipeline.py --tag vN --submit` chains: refuse uncommitted engine changes → `bankrec run --phase live` → `submit.py` → `live_report.py`.
- The browser crash ("insertBefore… not a child") is a client-side problem caused by browser translation or extensions. The API pipeline avoids it.

### 8.7 The FINAL gate (layer 4, `scripts/final_gate.py`)

It builds a label-free profile:
- share of lines allocated, UNIDENTIFIED rates, amount coverage and rows per line, overall, per side and **per entity**;
- the exception mix;
- from `<file>.stats.json`: stage mix per entity, advice-line resolution, masters folder and fx end date.

It compares that profile with the best scored LIVE file. Verdicts:
- **FAIL**: a new entity (ZA01) mostly unallocated or unidentified, or `masters_final` not used.
- **WARN**: allocation −3pp; an entity −10pp; UNIDENTIFIED ×2; an exception rate ×2.5 or ×0.4; advice resolution < 95 %; fx ending before June 2026.

Calibration (`final_gate.py calibrate`): on 4 scored LIVE files, the allocated share and amount coverage correlate
0.95 with the platform score. They catch **breakage** reliably, not subtle errors; layers 1–3 measure those.

### 8.8 FINAL-specific safeguards built in

- FINAL merges the previous masters into `masters_final`, so old names and IBANs survive renames and bank changes
  (`data._merge_previous_masters`, unit-tested).
- Advice lines with short numbers or only a PO resolve (the Sept-2025 layout fix).
- Payment runs join on quoted ids, so FX transfers in a new currency (ZAR) still join.
- Reproducible runs (pinned hash seed), and the stats sidecar for the gate.

---

## 9. Current status, results and known gaps

### 9.1 Status (2026-11-07 14:05)

| item | state |
|---|---|
| best engine | `main` = `9c3f786` + docs (tag `baseline-2026-11-07`). **TRAIN-month mean 91.225** (2025-03: 92.12, 2024-10: 90.33); hold-out 2025-05: 90.57 (at d08959f) |
| LIVE leaderboard | CuentaMinds **91.11** (live_v12). At 13:26: Overfitters 95.58 · Stressed Vibing 92.38 · Exponential 92.01 · Saturn Returns 91.92 · Equipo 9 91.17 |
| LIVE v12 breakdown | see `submissions/results/live_v12*.json`; v11: pair F1 ≈ 92.4 % (P 97.7, R 87.7) · line accuracy 92.3 % · exception macro-F1 84.6 % |
| uploads today | 11 / 20 (all through `pipeline.py --submit` since v9) |
| calibration | TRAIN eval − LIVE gap: 0.05–0.08 since the drift fix (`bankrec_calibration.tsv`) |
| Claude API | working (`claude-opus-5-5`); about $0.6 spent on analyst/skeptic/review calls |
| repo | pushed to GitHub (private), tag `baseline-2026-11-07` |

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
| drift fix | | 91.46 | 89.64 | **90.32** (live_v9) | advice short invoice numbers / PO (LIVE-only layout, neutral on TRAIN): **+2.42 on LIVE** |
| e935293 | | 91.67 | 89.87 | **90.69** (live_v10) | instalments (k/N) + round-share partials |
| d08959f | | 91.80 | 89.96 | **90.81** (live_v11) | one partial among quoted invoices; hold-out 2025-05 = 90.57 |
| 3d67c36 | | 92.09 | 90.23 | **91.11** (live_v12) | proforma advances, returned transfers, cheque returns, chargebacks, duplicate refunds |
| f62fe80 | | 92.04 | 90.24 | | skeptic-tightened flows (robustness, −0.02 within noise) |
| 9c3f786 | | 92.12 | 90.33 | **91.16** (live_v13) | cheque/lockbox stubs with short numbers (same resolver as advices) |
| 9f72a96 / ecf7053 | | 92.38 | 90.73 | **91.47** (live_v14) | PDF facts + hardening |
| 20375dc | | 92.70 | 91.03 | | unmatched known payer → `UNIDENTIFIED` exception when the cash is a round sum (×50) or quotes no document number |
| 06eca49 | | 93.67 | 93.08 | | **bug fix: payment runs grouped per debtor account** (run / end-to-end ids are reused across entities → wrong items + false FX_DIFFERENCE + false DUPLICATE_PAYMENT): **+1.51** |
| f499d8f | | 93.81 | 93.27 | live_v15 | multi-invoice receipt = due-date window of the payer's open items with one invoice left out |
| **99f29e7** | | **94.14** | **93.58** | **92.59** (live_v16) | early-payment discount on one invoice of a multi-invoice receipt (SHORT_DISCOUNT in window, PARTIAL late); unquoted single net-of-discount payments |
| **826dfb0** | | **94.32** | **93.73** | (not uploaded) | due-date window with two interior invoices left out (windows of 5–30); hold-out 2025-05 **93.07** (v16: 92.86) |
| **0f25a3e** | | **94.40** | **93.81** | | unexplained receipt that quotes open invoices of one customer (sum below the cash): allocate the quoted invoices, rest unallocated; hold-out **93.18**, 469 / 470 added pairs right → FINAL engine candidate |

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
| 5659c2b | +0.195 | **keep** | instalments: cash = total/N of one open invoice → PARTIAL |
| e935293 | +0.025 | **keep** | partials: cash = 25–70 % of one open invoice total |
| 91de62d | crash | timeout | one partial among candidates incl. unquoted (combinatorial, > 12 min/month) |
| d08959f | +0.110 | **keep** | one partial among the quoted invoices only |
| aec2b82 → 74d38ac | 0.000 | **kept (simpler)** | skeptic fixes: same-currency guard, combinatorial branch removed |
| 427f6d5 | +0.015 | discard | credit-note residual after quoted invoices (analyst #1). Most credit-note payments quote no refs |
| 3d67c36 | +0.280 | **keep** | unimplemented flows: advances, returned transfers, cheque returns, chargebacks, duplicate refunds (analyst #4) |
| 2d4ff40 → f62fe80 | −0.020 | **kept (robustness)** | skeptic-tightened flows: fewer misfire paths on unseen data |
| 9c3f786 | +0.085 | **keep** | cheque/lockbox stubs: short-number resolver |
| f46d9f3 | +0.005 | discard | SME rule: invoice − its related credit note = payment, 1:1 (already covered by the pair search) |
| 9f72a96 | **+0.330** | **keep** | **PDF facts**: instalment / retention / advance amounts, cancel & replace, cancelling and paid-out credit notes |
| 8506390 → ecf7053 | 0.000 | **kept (robustness)** | PDF facts hardened (skeptic): sanity checks, type keys, versioned cache, never-crash fallback |
| 20375dc | +0.310 | **keep** | unmatched known payer → UNIDENTIFIED when round sum / no document number (simulated +0.32 / +0.30 first) |
| 06eca49 | **+1.510** | **keep** | payment runs grouped per (debtor account, run/e2e id): ids collide across entities |
| (dup guard) | 0.00 | dropped | "already settled by a line that never quoted it → not a duplicate": +0.81 / +0.36 simulated before 06eca49, 0 after (same root cause) |
| f499d8f | +0.165 | **keep** | due-date window minus one interior invoice (hole) |
| 99f29e7 | +0.320 | **keep** | early-payment discount on one invoice of a multi-invoice receipt; single net-of-discount (first try crashed on NaN discount %, fixed) |
| 826dfb0 | +0.165 | **keep** | due-date window minus two interior invoices (short windows only); hold-out +0.21 |
| 0f25a3e | +0.080 | **keep** | quoted open invoices allocated on an unexplained receipt (funnel mechanism M1: reference present, set not closed); hold-out +0.11 |
| (earlier) | −0.50 | rejected | exact bitset subset-sum fallback for known payers: too many spurious exact sets |

### 9.4 Known gaps (LIVE v11 per-segment report, lines lost)

| segment | LIVE | TRAIN | lines lost | notes / ideas |
|---|---|---|---|---|
| `ar_ref_noise` | 92.2 % | 92.5 % | ~3.3k | known payers left unmatched; the paid invoices are *scattered* in the payer's open book (only 46 of ~700 TRAIN cases were contiguous), so no window rule works |
| `ar_1toN_credit_note` | 59.6 % | 60 % | ~1.3k | most payments quote no refs; units and residual ideas gave ≤ +0.015 |
| `ar_n_to_m` | 33.6 % | 61 % | ~1.2k | improved from 19.8 % (v9) → 33.6 % (v11); still a LIVE gap |
| `ar_cheque_deposit` | 63 % | 65 % | ~0.7k | short stub numbers fixed in 9c3f786 (not yet on LIVE) |
| `ar_1toN_text`, `ar_discount_late`, `ar_retention` | 89 / 33 / 48 % | | ~0.5–0.7k each | retention and advance-deduction rules live in PDFs (not parsed yet) |
| `ar_dispute_settlement` | 88.6 % | 99.7 % | ~150 | LIVE-only gap: likely advice drift downstream |

### 9.4b SME feedback (team finance/banking expert)

| SME point | verification in our code | outcome |
|---|---|---|
| a credit note references its invoice | `credit_notes.related_document` (structured); 95 % of netted notes point to an invoice on the same line | data available |
| payment = invoice − its credit note → net first, then match 1:1 | implemented exactly (`_netted_single`, f46d9f3): **+0.005**, because the pair search already matches invoice + credit note pairs | **rule verified, already covered** (not kept) |
| two lines: invoice in 1:1, credit note paid out separately | `try_credit_refund` (LIVE `ar_credit_note_paid_out` 88 %) + PDF "paid to your bank account" notes are never netted (pdfdocs) | handled; strengthened by the PDF facts |

Second round of team feedback (16:00), verified on TRAIN:

| point | finding | outcome |
|---|---|---|
| #1 commercial discount: an invoice paid in advance carries the early-payment discount | `ar_discount_late` was 23–35 % accurate: the discount was only recognised on a single picked invoice | multi-invoice + unquoted single → **+0.32** (99f29e7) |
| #2 incorrect match: a payment took an invoice, so the right payment cannot find it | exactly the false `DUPLICATE_PAYMENT` cases (P = 0.41 on 2024-10); root cause = payment-run ids reused across entities | bug fixed → **+1.51** (06eca49) |
| #3 big multi-invoice payments: "sum of invoices = payment first, else oldest" | missed sets do sum to the cash to the cent; they are due-date windows with an invoice left out | window-minus-one search → **+0.165** (f499d8f) |
| gap analysis G3: UNIDENTIFIED exception missing on unmatched known payers | round sums / no document number are truly unidentified | **+0.31** (20375dc) |

The remaining credit-note loss is **multi-invoice payments without references**, which needs a different idea (open question to the SME, §8 of the review pack).

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
| now → 15:30 | autoresearch loop (analyst → experiment → skeptic → guard → promote). After each kept batch: fast-forward `main`, `pipeline.py --tag vN --submit`, add a calibration row |
| **15:30** | **code freeze.** `git checkout main && git tag final-engine && git push --tags` |
| 15:30–17:30 | video, write-up, presentation (§12) |
| 17:30 | **stop all experiments** (they slow FINAL). Clean `git status` on `main`. Laptop on power |
| **18:00** | FINAL → §11 |
| ~18:45 | upload FINAL **once** |
| 19:00 | make the repo public, fill in the Project page, **Save** |

---

## 11. FINAL runbook (18:00 → 19:30)

> Goal: one **valid** file uploaded by **~18:45**. Run on `main` at the `final-engine` tag, with nothing else running.
>
> **One command does steps 1–7, with the checkpoint:** `.venv/Scripts/python scripts/final_run.py --download` (PowerShell: `docs/RUNBOOK_POWERSHELL.md` §10). It downloads and checks the
> SHA-256 against the site's public `config.json`, extracts, sanity-checks, runs, gates, and gets the Claude
> release-manager review. It stops before uploading. The manual steps below are the same procedure, for when anything needs a hand.

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
5. **Check:** `"ok": true`, then the **readiness gate**:
   `.venv/Scripts/python scripts/final_gate.py check final submissions/final_v1.csv.gz`.
   - **PASS**: upload.
   - **WARN**: read the flagged entity, exception or advice line and decide (upload if it's explainable drift, e.g. slower payments in 2026).
   - **FAIL**: fix it (one iteration only; it must finish by ~19:05), re-run and re-gate.

   Stage counts should be proportional to LIVE: about 203k lines, payrun ~54k, party ~61k, ref ~39k, unidentified ~2.5k.
6. **Upload once.** Either Pratyush uploads it on **Enviar** (Submit) in an Incognito window, or, with his explicit go,
   `.venv/Scripts/python scripts/submit.py FINAL submissions/final_v1.csv.gz --i-confirm-final-is-one-shot`. That
   re-validates, and refuses if a FINAL submission is already scored. If validation rejects it, fix it and re-upload. Once a file is accepted, stop.
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
| 11-07 12:25 | cf56148 | LIVE v8 = 87.90; upload + feedback pipeline (`submit.py`, `live_report.py`, `pipeline.py`) |
| 11-07 12:30 | c30a802 | FINAL readiness gate (`final_gate.py`), run stats sidecar (`<file>.stats.json`), guide §8.4 |
| 11-07 12:35 | f8b4a36 | **LIVE v9 = 90.32** (+2.42, drift fix confirmed), first upload through `pipeline.py --submit` |
| 11-07 13:05 | d08959f | instalments, partial shares, one partial among quoted invoices (eval 90.88); **LIVE v10 = 90.69** |
| 11-07 13:20 | a4c5b00 | evaluation strategy (§8.4): calibration, Claude analyst personas, overfit guard, FINAL orchestrator, FINAL masters merge |
| 11-07 13:30 | 88e7274 | **LIVE v11 = 90.81** (pred 90.65); hold-out 2025-05 = 90.57 |
| 11-07 13:40 | 3d67c36 | unimplemented flows (+0.28): proforma advances, returned transfers, cheque returns, chargebacks, duplicate refunds |
| 11-07 13:46 | f62fe80 | skeptic-tightened flows (robustness) |
| 11-07 13:58 | 9c3f786 | cheque/lockbox short stub numbers (+0.085) |
| 11-07 14:01 | – | **LIVE v12 = 91.11** (pred 91.08) |
| 11-07 14:05 | 5925988 | README.md; guide brought fully up to date; baseline tag `baseline-2026-11-07` |
| 11-07 14:25 | – | **LIVE v13 = 91.16** (pred 91.14); SME review pack |
| 11-07 15:10 | 9f72a96 | **PDF facts** (+0.33): instalments, retention, advance, cancel & replace, cancelling / paid-out credit notes; SME credit-note rule verified |
| 11-07 15:20 | ecf7053, 34e6f22 | PDF facts hardened (skeptic); **upload checkpoint** (LIVE y/N, FINAL type UPLOAD); `docs/RUNBOOK_POWERSHELL.md` |
| 11-07 16:35 | – | **LIVE v16 = 92.59** (+1.12). TRAIN→LIVE gap widened to 1.27: the payment-run id collisions are far more frequent in the merged TRAIN months than in LIVE (AP segments were already right on LIVE), so most of that +1.51 is TRAIN-only; LIVE gains came from discounts, credit notes and the UNIDENTIFIED exception (macro-F1 0.864 → 0.887). The checkpoint now uses the wider gap |
| 11-07 17:40 | `scripts/final_profile.py`, `final_run.py`, `runbook.ps1 -Phase fixloop` | **agentic FINAL loop**: input-parser agent (label-free raw-input drift vs LIVE: new entity / format / bank / currency, bank-text layouts, document-number shapes, side folders; LIVE vs TRAIN flags only ZA01) runs before the engine, its `[WARN]` flags feed the release-manager review and the UPLOAD checkpoint (advisory, never blocks). `fixloop`: findings → one Claude Code fix in nova7nov (simulate, dev eval keep / revert) → hold-out audit in the clone → FINAL rebuild → checkpoint (~30 min per loop; only before UPLOAD) |
| 11-07 17:15 | 826dfb0, `scripts/bankrec_funnel.py` | two-hole window kept (+0.165 dev, +0.21 hold-out) → FINAL engine candidate. Failure funnel (TRAIN 2025-03 / 2024-10; ar_ref_noise, ar_1toN_credit_note, ar_n_to_m, ar_dispute_settlement): ~85 % of lost pairs are lines left **unmatched with no usable reference** (4309 / 4566 pairs), almost all big multi-invoice receipts (≥5 items: 330 lines = 4319 pairs) whose golden set is a scattered subset of the payer's open book with an exact sum, so any exact-sum search is ambiguous (the bitset subset-sum fallback already lost −0.50). Other set chosen 567 / 729, consumed by another line 119 / 96, amount wrong 32 / 39. Fixed the narrow M1 case only (0f25a3e, simulated first with the official scorer, then evaluated: +0.08 dev, +0.11 hold-out). `runbook.ps1` LIVE / FINAL phases now start by syncing the clone to `-Engine` (default `main`, fallback `live-v16`) and running the tests. Untouched month 2025-04 (never tuned on): v16 93.68 → 48f39a1 **93.92**; +1074 correct pairs on 200 lines, +409 wrong pairs on 47 lines, 27 previously correct lost; precision 0.9836 → 0.9813, recall 0.9026 → 0.9108 |
| 11-07 16:20 | 20375dc..99f29e7 | **LIVE v14 = 91.47**; team gap analysis verified: UNIDENTIFIED rule, payment-run account bug (+1.51), window-minus-hole, discounts: **TRAIN eval 91.555 → 93.86** |
