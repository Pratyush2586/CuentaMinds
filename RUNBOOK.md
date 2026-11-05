# closeops build-day runbook

Two people: **Pratyush**: data inspection, mapping sign-off, leaderboard submissions,
domain sanity checks, demo. **Claude Code**: code, tests, fixes, agents, exporter.
Goal: first leaderboard submission within ~90 min of getting the data (rules only),
then iterate. Everything is a dry run: closeops only *proposes* JEs and payments.

All commands run from the repo root in PowerShell or Git Bash. `closeops` is
`.venv/Scripts/closeops` (or `.venv/Scripts/python -m closeops.cli`).

---

## 1. Setup on a fresh machine (Windows)

```powershell
git clone <repo> nova7nov; cd nova7nov; git checkout main
py -3.12 -m venv .venv                      # system python may be 3.11: always use py -3.12
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -q           # expect all green in ~15 s
.venv/Scripts/closeops synth data/demo --seed 7
.venv/Scripts/closeops run data/demo --mapping config/mappings.synth.yaml
```

Agents (optional, off by default, never needed for a submission):

- `ant auth login` (Anthropic CLI profile), or `$env:ANTHROPIC_API_KEY = "..."` in the
  shell that runs closeops / Streamlit. Never commit a key.
- Smoke test: `closeops run data/demo --mapping config/mappings.synth.yaml --agents`.
  If credentials are missing the run prints `note: agents unavailable: ...` and the
  result is the rules-only result (circuit breaker).
- Models: `claude-sonnet-5-5` (triage roles), `claude-opus-5-5` (Controller memo).

Dashboard: `.venv/Scripts/streamlit run dashboard/app.py` (opens on localhost:8501).

---

## 2. Day-of timeline (spec §5)

| Time | Who | What | Commands |
|---|---|---|---|
| 0:00–0:10 | both | Read brief + scoring rules. Write down: submission format, how findings are identified (record ids? file+row?), which dimensions are scored. | — |
| 0:00–0:10 | Claude | Unpack data to `data/raw/` (git-ignored), profile it. | `closeops profile data/raw > data/profile.md` |
| 0:10–0:30 | Claude drafts, Pratyush signs off | Copy `config/mappings.synth.yaml` → `config/mappings.event.yaml`, map from the profile (section 3 checklist). Set `period_end`. | `closeops run data/raw --mapping config/mappings.event.yaml --out results` (read every `note:` and every `source column ... not in` warning) |
| 0:10–0:40 | Claude | Write the event exporter (section 4) with a test against the published format. | `.venv/Scripts/python -m pytest tests/export -q` |
| 0:30–1:30 | both | Rules-only run → export → **first leaderboard submission**. Log it (section 5). | `closeops run data/raw --mapping config/mappings.event.yaml` then export, submit |
| 1:30–4:00 | both | Iterate per leaderboard dimension; one change per submission; log each. Turn on agents for residuals once the rules baseline is logged. | `closeops run ... --agents` |
| 4:00–5:30 | both | Data-specific checks found on the day; tune policy (rules, never rows; section 6). | edit `config/policy.yaml`, re-run |
| 5:30–end | Pratyush leads | Freeze features. Rehearse demo twice (section 7). Final submission. | `streamlit run dashboard/app.py` |

Cut order if short of time (bottom first): agents → dashboard polish → exporter
niceties → never cut ingest + rule checks.

If the data has PDFs (`PDF files: N` at the end of the profile): write a
`pdfplumber` → Claude structured-extraction adapter that emits a CSV with invoice
columns, then map that CSV like any other file. Budget 45 min; skip if < 10 % of invoices.

---

## 3. Mapping checklist

A mapping is `period_end` (plus optional `date_order`) and, per canonical table, one
spec or a **list of specs** (one per file layout, e.g. one per bank). A spec has:

| Key | Meaning |
|---|---|
| `files` | glob list, relative to the raw dir (`.csv`, `.tsv`, `.txt` delimited; `.xlsx`/`.xlsm`; `.json` records) |
| `sheet` | xlsx sheet name (default: first sheet) |
| `skip_rows: N` | lines (csv/txt) or sheet rows (xlsx) before the header, e.g. a bank preamble; `source_row` stays the physical line |
| `columns` | canonical → source header |
| `defaults` | canonical → constant, e.g. `entity: DE01` for a bank file without an entity column |
| `date_order: dmy` / `mdy` | how `03/10/2026` reads (default `dmy`); the top-level value applies to every spec, a spec can override it |
| `amount_from: {debit: <col>, credit: <col>}` | bank amount = credit − debit (money in positive, out negative); an empty side counts 0 |
| `sign_col: <col>` + `sign_debit_values: [D, DR, "-"]` | amount made negative where the indicator matches (case-insensitive) |
| `id_prefix_entity: true` | rewrite invoice_id / txn_id / je_id / po_id (and po_id on invoices and goods_receipts) as `<entity>:<id>`; goods_receipts has no entity column, so give it `columns: {entity: <col>}` or `defaults: {entity: ...}` |

```yaml
date_order: dmy
tables:
  bank_transactions:
    - files: ["bank_DE01*.csv"]
      skip_rows: 2
      columns: {txn_id: "Ref", txn_date: "Buchungstag", counterparty_iban: "IBAN"}
      amount_from: {debit: "Soll", credit: "Haben"}
      defaults: {entity: DE01, currency: EUR}
    - files: ["bank_US01*.txt"]
      date_order: mdy
      columns: {txn_id: "Id", txn_date: "Date", amount: "Amount", entity: "Company"}
      sign_col: "D/C"
      sign_debit_values: [D, DR]
```

Unmapped canonical columns load empty; unmapped source columns are kept as `x_<name>`.
Every text cell is whitespace-stripped (` DE01 ` → `DE01`). Amount columns (including
`qty_received` and `payment_terms_days`: `Net 30` → 30, `3 pcs` → 3) accept `1.234,56`,
`1,234.56`, `21%`, `(500)`, `500.00-`; dates accept dd/mm/yyyy, dd.mm.yy (yy < 70 → 20yy),
dd.mm.yyyy and ISO.

Ingest notes (printed as `note:` lines by `closeops run`):

- `invoices.invoice_date: 412/600 unparsed`: non-empty cells that did not parse (they
  load empty). Fix the column mapping, `date_order` or the source before trusting that
  check family.
- `invoices.invoice_id: 12 ids repeat across entities ...`: two entities use the same id;
  set `id_prefix_entity: true` on every table carrying those ids (invoices,
  purchase_orders, goods_receipts, bank_transactions, gl_entries). Findings then carry
  `DE01:<id>`; the event exporter must strip the prefix if the format wants source ids.
- `closeops profile` ends with **Files not profiled**: every file it could not read
  (unsupported type, broken file, PDFs), with the reason.

### Which columns each check needs

| Check | Tables | Canonical columns that must be mapped |
|---|---|---|
| dup_exact / dup_near / dup_cross_entity | invoices (+vendors) | invoice_id, entity, vendor_id, **invoice_no**, invoice_date, gross_amount, net_amount (credit-note skip); vendors.vendor_id+name (same vendor by name) |
| dup_payment | bank_transactions | txn_id, entity, txn_date, amount (signed, outgoing < 0), **counterparty_iban**, currency |
| tax_math | invoices | net_amount, tax_amount, gross_amount |
| tax_amount | invoices | net_amount, **tax_rate**, tax_amount |
| tax_reverse_charge | invoices (+tax_rates) | vendor_country, **buyer_country**, tax_amount, tax_rate |
| tax_rate | invoices, tax_rates | buyer_country, tax_rate; tax_rates.country, rate, kind (`standard`, `zero`/`exempt` matter) |
| tax_missing_vat_id | invoices, vendors | vendors.vat_id |
| iban_changed_before_payment | bank_transactions, vendor_bank_history | counterparty_iban, txn_date, amount; history.vendor_id, iban, changed_at (changed_by for evidence) |
| iban_not_in_master / iban_country_mismatch | bank_transactions, vendors | counterparty_iban, counterparty_name; vendors.vendor_id, iban, country |
| shared_iban | vendors | vendor_id, iban, entity |
| split_invoices | invoices | vendor_id, invoice_date, net_amount, gross_amount (policy `fraud.approval_limit`) |
| po_price_mismatch / no_po | invoices, purchase_orders | invoices.**po_id**, unit_price; purchase_orders.po_id, unit_price |
| qty_over_received | invoices, purchase_orders, goods_receipts | invoices.qty; goods_receipts.po_id, qty_received |
| contract_price_mismatch | invoices, contracts | invoices.vendor_id, item, unit_price, invoice_date; contracts.vendor_id, item, unit_price, valid_from, valid_to |
| bank_unmatched (bank rec) | bank_transactions, invoices, vendors, gl_entries, accounts, fx_rates | bank: txn_id, entity, txn_date, amount, currency, counterparty_iban/name, reference; invoices: entity, vendor_id, invoice_no, invoice_date, gross_amount, currency; **accounts.type = "bank"** for the GL pass |
| je_unbalanced / tb_unbalanced | gl_entries | je_id, entity, debit, credit |
| intercompany_mismatch | gl_entries (+accounts) | entity, **counterparty_entity**, account, debit, credit, currency; accounts.type containing "intercompany" narrows it |
| cutoff | invoices | invoice_date, **service_period_end**, and the right `period_end` |
| payment proposals | invoices, vendors, contracts | invoices.**status** (open/paid), vendors.iban, contracts.payment_terms_days |
| amount_eur / materiality | fx_rates | date, currency, rate_to_eur (EUR needs no row) |

### What silently degrades if something is missing (measured, hard seed 2026)

Baseline 95 tp / 0 fp / 0 fn. Each row drops one table or column from the mapping.

| Missing | Result (tp/fp/fn) | Effect |
|---|---|---|
| invoices.po_id (PO table present) | 72/0/23 | `no_po` skipped (`note: skipped no_po: po_id column empty`); PO price / qty checks cannot link, so they are lost. (Before the guard: 95/612/0.) |
| gl_entries table | 93/**55**/2, P=0.63 | receipts and fees become `bank_unmatched`; intercompany lost |
| accounts table, or no account typed `bank` | 95/**55**/0, P=0.63 | bank-rec GL pass is off: same fp flood. Map `type` or add `defaults` |
| invoices.tax_rate | 95/0/0 | rate derived as tax/net per file, snapped to a valid country rate within `tax.tolerance` (`note: tax: tax_rate empty in ...`); `tax_amount` is skipped there, wrong amounts show as `tax_rate`. (Before the guard: 87/47/8.) |
| vendor_bank_history | 90/3/5 | all `iban_changed_before_payment` missed; old IBANs look unknown |
| purchase_orders | 72/0/23 | all PO checks skipped (note printed) |
| invoices.invoice_no | 73/0/22 | all invoice duplicate checks dead |
| invoices.invoice_date | 75/0/20 | dup_near, cutoff, split, contract checks dead |
| bank counterparty_iban | 85/0/10 | dup_payment and IBAN fraud checks dead |
| invoices.buyer_country | 87/0/8 | tax_rate / reverse charge dead |
| service_period_end | 89/0/6 | cutoff dead |
| gl counterparty_entity | 93/0/2 | intercompany dead |
| goods_receipts / contracts / tax_rates / vendors | −5 / −5 / −4 / −3 recall | that check family skipped |
| invoices.status | 95/0/0, 453 payment proposals vs 296, **all HOLD** (`status_unknown`) | without status, invoices not matched in the bank are proposed but never AUTO |
| fx_rates, bank reference/name, invoices.entity, vendor_country | no change | (safe to skip under time pressure) |

Rules of thumb: fp floods come from a *present table with a missing link column*
(the bank account type is the one left unguarded); recall losses come from missing
tables or link columns. Any `note:
skipped ...` line in `closeops run` output is a check that did not run.

---

## 4. Exporter-on-the-day checklist

Contract (`src/closeops/export/event.py`):

```python
def export_event(run: RunResult, out_dir: Path) -> list[Path]: ...
```

`RunResult` gives `findings` (list of `Finding`: check, category, severity, entity,
record_type, record_ids, evidence, confidence, tier, requires_human_review,
explanation, resolved_by; `finding_id`, `to_dict()`), `journal_entries` and `payments`
(DataFrames), `side_tables` (`bank_matches`, `match_status`, `trial_balance`),
`dataset` (all canonical tables with `source_file`/`source_row`), `notes`, `run_dir`.

1. Read the published submission format; copy one example file into
   `tests/export/fixtures/`.
2. Write `tests/export/test_event.py` first: run `run_close` on
   `generate(tmp, seed=7, n_invoices=200)`, call `export_event`, assert exact column
   names/order, one row per finding (soft checks excluded unless asked), ids are the
   *source* ids, file encoding/delimiter as specified, and it parses with the official
   validator if one is given.
3. Implement; drop `SOFT_CHECKS` (`closeops.checks.fraud`) unless the format wants them.
   Map our categories to theirs in one dict at the top of the file.
4. Wire it: it is **not** called by the pipeline yet. Add a call after `write_results`
   (or a `closeops export RUN_DIR` command) behind a flag, so the generic export stays.
5. Before every submission open the file once by hand (first 5 rows, row count).

---

## 5. Submission log (`scoreboard.md`)

Every local score appends one row:

```
| time | run_dir | precision | recall | f1 | tp | fp | fn | note |
```

```powershell
closeops score results/<run> data/<key>.json --note "what changed since last row"
```

`score` prints overall, per category and per check. On the day there is no answer key:
log leaderboard submissions by hand in the same table (`run_dir` = the run you
submitted, P/R/F1 columns = the leaderboard dimensions you get back, note = the one
change you made). One change per submission, so each row explains a movement. Commit
`scoreboard.md` after each submission.

---

## 6. Policy tuning knobs (`config/policy.yaml`)

Tune on rules, never on specific rows. Re-run and log after each change.

| Key | Default | Raise it → | Lower it → |
|---|---|---|---|
| duplicates.date_window_days | 7 | more near-duplicates caught, more recurring-billing fps | misses slow re-submissions |
| duplicates.invoice_no_similarity | 85 | fewer near-dup fps, misses typo'd numbers | catches more variants, sequential numbers start to match |
| duplicates.payment_window_days | 3 | more duplicate payments, standing orders may clash | misses late duplicates |
| tax.tolerance | 0.02 | ignores rounding noise, misses tiny tax errors | flags rounding |
| tax.eu_countries | EU-27 | — | drives reverse-charge logic: keep accurate |
| fraud.iban_change_window_days | 30 | more changed-IBAN holds (fps on legit changes) | misses changes a month+ before payment |
| fraud.approval_limit | 10000 | fewer split flags | more split flags. Set to the company's real limit if given |
| fraud.split_window_days | 7 | more split clusters | fewer |
| fraud.round_amount_min / export_soft_signals | 5000 / false | soft signals are never scored; export only if asked |
| match.price_tolerance_pct | 2 | fewer PO/contract price flags | more. Set to the brief's tolerance if stated |
| match.qty_tolerance | 0 | tolerates partial-delivery noise | strict |
| bankrec.date_window_days / max_payment_lag_days | 5 / 90 | more matches, more wrong matches | more bank_unmatched |
| bankrec.max_group / amount_tolerance / fx_tolerance_pct | 4 / 0.01 / 1 | more group / fx matches (slower, riskier) | stricter |
| materiality_eur | 5000 | fewer HOLDs, more AUTO | more items to humans |
| tiers.auto_min_confidence / hold_below_confidence | 0.9 / 0.6 | (auto) fewer AUTO; (hold) more HOLD | the reverse |
| tiers.auto_categories | duplicate, tax, match, bankrec | — | fraud is never AUTO; IBAN findings are always HOLD (hard rule in code) |
| accounts.* | 4000/4720/... | account numbers used in proposed JEs: set to the event's chart of accounts | |
| agents.max_residuals | 40 | more findings reviewed by agents (cost, time) | only the largest residuals by amount_eur go to agents; the rest keep the rule result (one note) |
| agents.wall_clock_seconds | 300 | longer agent phase | no new agent calls after the budget; in-flight results still apply (one note) |

---

## 7. Demo script (3 min, spec §4.8)

Setup before going on stage: `git checkout -- results/demo` (undoes decisions or runs
written there during rehearsal, so the committed evidence chain verifies), then
`streamlit run dashboard/app.py`; sidebar **Raw data dir** = `data/demo` (or
`results/demo/raw`), **Results dir** = `results` (or `results/demo`). Changing Results
dir switches to the newest run there. The Evidence chain tile reads *verified*,
*BROKEN* or *missing* (no evidence.jsonl in the run dir). Human approve / reject are
logged with tier `HUMAN_APPROVED` / `HUMAN_REJECTED`.

| Time | Say | Click |
|---|---|---|
| 0:00 | "Month-end close for three entities: messy CSV/XLSX/JSON, three date and number formats, three banks." | Show `data/demo` file list, or the profile |
| 0:20 | "One click runs the close: ingest, rules, policy, evidence log." | Sidebar **Run close** (Agents toggle on only if credentials work); the status box lists the stages |
| 0:40 | "It found the planted traps." | **Overview**: findings, HOLD count, bank reconciled %, *Evidence chain verified ✓* |
| 1:00 | "Duplicate invoice: same vendor, same amount, `RE3383` re-entered a day later as `re 3383`." | **Exceptions** → Category `duplicate` → drill into `dup_exact:D100098\|D100082` → Evidence + Source rows (`invoices.csv:99`, `:83`) |
| 1:20 | "Wrong VAT: a German invoice at 16 %, the expired rate (valid: 19 / 7 / 0)." | Category `tax` → `tax_rate:D100349` → expected vs booked tax |
| 1:40 | "Changed bank details: the IBAN was changed by k.weber 25 days before this payment, so it is *held*, never auto-paid." | Category `fraud` → `iban_changed_before_payment:BK-ES01-00020` (old/new IBAN, changed_by, days_between) → **Journal & Payments**: red rows, `iban_changed` block reason |
| 2:10 | "Every number traces to a file and row; the log is hash-chained." | Back in the drill-down: Source rows table |
| 2:30 | "Humans decide the holds." | **Human review** → Approve one finding, Reject one payment → the HOLD metric on Overview shows fewer awaiting review |
| 2:45 | "The close report: per entity, TB balanced, reconciled %, N items for human review." | **Close report** (with agents: the Controller's close memo in the run dir, `close_memo.md`) |

The ids above are from the committed run `results/demo/20261105-200049` (seed 7). A
fresh `closeops synth data/demo --seed 7` regenerates the same data, so the ids hold.

---

## 8. Fallbacks

- **Rules only**: leave the Agents toggle off / drop `--agents`. This is the kill switch
  and also what a failed credential check falls back to automatically.
- **Pre-recorded run**: `results/demo/` is committed (everything else in `results/` is
  ignored): `results/demo/20261105-200049/` (seed 7, rules only) and `results/demo/raw/`
  (its input). In the dashboard set Results dir = `results/demo`, Raw data dir =
  `results/demo/raw`; do **not** press Run close (that would write a new run there).
  `.gitattributes` keeps those bytes exact so the evidence chain still verifies.
  Approving / rejecting there writes into the committed run: `git checkout -- results/demo`
  restores it.
- **A stage fails on the day**: proposals, evidence logging and amount fills are guarded;
  a failure prints `note: error in <stage>: ...` and the findings still export (empty JE /
  payment files). Fix the cause, re-run.
- **Agents misbehave**: a bad request or unknown model trips the breaker like a missing
  key (`note: agents unavailable: ...`); the result is rules-only.
- **Mapping trouble on the day**: map only invoices + bank_transactions + vendors first
  (covers duplicates, tax math, IBAN checks), submit, then add tables one by one.
- **Exporter trouble**: submit `findings.csv` from the run dir after renaming columns
  by hand in the format's header order.

---

## 9. Rehearsal record (2026-11-05)

Data: `closeops synth data/rehearsal/raw --seed 2026`, then
`python scripts/rehearsal_scramble.py data/rehearsal/raw data/rehearsal/unseen`
(renames + reorders the columns of invoices, vendors, bank statements and GL, renames
those files, moves the answer key out). Mapping written from the profile only:
`config/mappings.rehearsal.yaml`.

| Step | Command | Wall-clock |
|---|---|---|
| profile | `closeops profile data/rehearsal/unseen > data/rehearsal/profile.md` | 1 s |
| write mapping (from profile.md only) | `config/mappings.rehearsal.yaml` | 30 s |
| run | `closeops run data/rehearsal/unseen --mapping config/mappings.rehearsal.yaml --out results/rehearsal` | 2 s (pipeline 1.6 s) |
| score | `closeops score results/rehearsal/<run> data/rehearsal/answer_key.json --note ...` | 1 s |
| **total** | 19:56:14 → 19:56:57 | **0.7 min**, result 90/0/0 (P=R=1.000) |

That is Claude at the keyboard with a column-renamed copy of a familiar dataset. Plan
for 20–30 min on the day: real data adds unknown tables, missing columns, and
Pratyush's sign-off of every mapped column.

Friction found, and what was done:

1. **`closeops profile ... > file` crashed** on names outside cp1252 (e.g. `Łódź`): piped
   stdout used the Windows code page. Fixed: the CLI always writes UTF-8
   (test `tests/test_cli.py::test_profile_piped_stdout_handles_any_unicode`). Before the
   fix the profile file also showed `Soci�t�` (cp1252 bytes) when read as UTF-8.
2. `closeops run` did not show which checks were skipped. Fixed: it prints `note: ...`
   lines (`test_run_prints_notes`).
3. `closeops score` showed categories only; per-check movement was invisible. Fixed:
   prints a `by check:` block (`test_score_prints_by_check`).
4. Profile dtype says `number` for ids such as `DE01`, `PO450001`, `V10007` (lenient
   amount parser). Not fixed: ignore the dtype column for id-like text (Wishlist).
5. Ambiguous pairs need a human call: `Book. Dt` vs `Val. Dt` (use booking date),
   `Bank Ref` vs `Narrative` (txn id vs remittance reference). Pratyush signs these off.
6. A mistyped source column only logs `source column '...' not in <file>` and the
   column loads empty. Watch stderr on the first run (Wishlist: a mapping validator).

---

## 10. Known weaknesses

Hard mode (`closeops synth DIR --seed 2026 --hard`) adds boundary traps (2.5 % PO price
variance vs 2 % tolerance; exact and near duplicate 6 days later vs a 7-day window;
IBAN change 28 days before payment vs 30), boundary decoys (1.9 % variance, IBAN change
35 days before) and recurring-billing decoys (same gross every month, sequential
invoice numbers, standing-order payments without the invoice number). Scored on
2026-11-05: **overall P = 1.000, R = 1.000 (95/0/0); every check 1.000** (row in
`scoreboard.md`). The scored checks hold at the boundaries; the weaknesses it exposed
are outside the score, or are mapping-dependent:

| Check / area | Example | Suggested fix |
|---|---|---|
| bank reconciliation (`exact` pass) on recurring billing | hard seed 2026: Oct standing order for Sep invoice D100224 (3,476.69, no invoice no. in reference) was matched to the Oct invoice D100437 (same gross, nearest date). D100224 shows unmatched, D100437 shows paid. | When several open invoices of the linked vendor have the same amount, prefer the oldest open one (FIFO) or leave it to the Reconciler agent instead of nearest-date. |
| payment proposals without `invoices.status` | Same data with `status` unmapped: 453 proposals instead of 296; every invoice settled before the statement period looks open. Guarded: all are HOLD `status_unknown`, none AUTO. | Treat invoices whose due date is before the first bank date as settled, and use the GL AP clearing, to shrink the HOLD list. |
| bank_unmatched (GL pass) depends on `accounts.type == "bank"` | Unmapped accounts table → 55 fps (receipts, fees). | Fall back to accounts named/typed like cash/bank, or the GL rows that mirror the bank amount on the same day. |
| no_po when `invoices.po_id` is unmapped | Guarded: `no_po` skipped with a note (was 612 fps). PO price / qty checks still need the link. | Map po_id, or match invoice → PO by vendor + item + amount. |
| tax when `invoices.tax_rate` is unmapped | Guarded: rate derived as tax/net (was 47 fps). `tax_amount` cannot fire on derived rows. | Map tax_rate when it exists. |
| hard thresholds | Boundary traps sit 0.5 % / 1 day / 2 days from the policy limits. If the benchmark's tolerances differ, whole check families flip. | Read tolerances from the brief and set them in `policy.yaml` first thing; log the change. |
| duplicates (unstressed) | Sequential numbers (`RE-02681`, `RE-02682`) have similarity ≥ 85 and the same gross; only the 7-day window keeps monthly billing out of `dup_near`. A weekly recurring vendor would be flagged. | Require the date gap ≠ the vendor's usual billing interval, or exclude when the numbers are consecutive integers. |

---

## 11. Wishlist (out of scope for now)

- `closeops check-mapping RAW MAPPING`: list unmapped canonical columns per table,
  mistyped source headers, and the checks each gap disables (the table in section 3,
  computed).
- Mapping suggestions in `profile`: fuzzy-match source headers to canonical names and
  print a draft YAML.
- Profile dtype: mark id-like text (`DE01`, `PO450001`) as text, not number.
- `closeops profile --out FILE` (UTF-8) so nobody depends on shell redirection.
- `closeops export RUN_DIR` once the event exporter exists.
- Hard-mode extras: weekly recurring vendors, IBAN-change decoys at 29/31 days, tax
  rounding at the tolerance.
