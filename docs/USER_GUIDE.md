# closeops — User Guide

How to install, test, run, look at the results, and evaluate the autonomous finance close.
For the build-day timeline and demo script, see `RUNBOOK.md`.

All commands are run from the repo root (`nova7nov/`) in Git Bash or PowerShell on Windows.
`C=.venv/Scripts/closeops` below is shorthand. In PowerShell use `.venv\Scripts\closeops`.

---

## 1. One-time setup

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

Optional, needed only for the Claude agents (`--agents`):

```bash
ant auth login            # or: set ANTHROPIC_API_KEY in your environment
```

Without credentials everything still works rules-only. With `--agents` and no credentials, the run adds a single `agents unavailable` note.

---

## 2. Test that it works

```bash
.venv/Scripts/python -m pytest -q                 # full suite: expect "286 passed" (~25 s)
```

Run one area at a time when you change something:

| What | Command |
|---|---|
| Value parsing and file loading | `.venv/Scripts/python -m pytest tests/ingest -q` |
| All checks (dups, tax, fraud, PO, bank rec, GL) | `.venv/Scripts/python -m pytest tests/checks -q` |
| Journal entries and payment holds | `.venv/Scripts/python -m pytest tests/test_journal.py -q` |
| Claude agent layer (fake LLM, no network) | `.venv/Scripts/python -m pytest tests/agents -q` |
| End-to-end, incl. seed-7 = 90/0/0 regression | `.venv/Scripts/python -m pytest tests/test_e2e.py -q` |
| Messy-data end-to-end | `.venv/Scripts/python -m pytest -q -k ugly` |
| Dashboard | `.venv/Scripts/python -m pytest tests/test_dashboard.py -q` |

---

## 3. Run a close end to end (about 2 seconds)

**Step 1 — make practice data.** This writes 14 messy raw files plus `answer_key.json`, the 90 planted problems.

```bash
$C synth data/try --seed 7            # add --hard for the harder measurement set
```

**Step 2 — look at the raw files** (this is what you'll do first with the real data):

```bash
$C profile data/try > data/try_profile.md
```

For each file, this lists its columns, types, null %, sample rows and candidate key columns.

**Step 3 — run the close:**

```bash
$C run data/try --mapping config/mappings.synth.yaml --out results
```

It prints the run folder and a one-line summary, for example:

```
results/20261105-213657
90 findings (AUTO=26, AUTO_NOTE=43, HOLD=21)
```

- **AUTO**: the system is confident and the item is below materiality, so it acts.
- **AUTO_NOTE**: it acts and flags the item for later review.
- **HOLD**: a human must decide. Changed bank details, high-risk fraud, large amounts and low confidence always land here.

Add `--agents` to let the Claude agents review the borderline items.

---

## 4. See the results

### Files in the run folder (`results/<timestamp>/`)

| File | What it is |
|---|---|
| `close_report.md` | **Start here.** Per entity: findings by category, % bank lines reconciled, trial balance yes/no, HOLD count, run notes |
| `findings.csv` / `.json` | Every issue: check, category, severity, tier, confidence, EUR amount, and `source_refs` (`file:row` of the raw evidence) |
| `payment_proposals.csv` | Open invoices to pay. `tier` is AUTO or HOLD, and `block_reason` says why (e.g. `iban_changed_before_payment`, `dup_exact`, `status_unknown`) |
| `journal_entries.csv` | Proposed adjusting entries (reversals of duplicates, VAT corrections, accruals/deferrals, suspense). Each one balances |
| `bank_reconciliation.csv` | Which bank line matched which invoice or journal entry, and how (`reference`, `exact`, `window`, `group`, `fx`) |
| `match_status.csv` | PO / receipt / invoice match status per invoice |
| `trial_balance.csv` | Balances per entity and account |
| `evidence.jsonl` | Tamper-evident audit log: every decision, hash-chained |
| `policy_used.yaml` | The exact thresholds this run used |
| `close_memo.md` | Controller agent's sign-off memo (only when run with `--agents` and credentials) |

Check the audit log hasn't been edited:

```bash
.venv/Scripts/python -c "import sys; from closeops.evidence import EvidenceLog; print(EvidenceLog.status(sys.argv[1]))" results/<timestamp>/evidence.jsonl
# verified | broken | missing
```

### The dashboard (what judges see)

```bash
.venv/Scripts/streamlit run dashboard/app.py      # opens http://localhost:8501
```

1. **Sidebar:** the fields are Raw data dir (default `data/demo`), Mapping, Policy, Results dir and the **Agents** toggle (off = rules only).
   - Click **Generate demo data** the first time.
   - Then click **Run close**.
   - With no new run, the dashboard opens the newest folder in Results dir.
   - Set Results dir to `results/demo` to show the pre-recorded fallback run.
2. **Overview:**
   - findings by category
   - bank lines reconciled %
   - HOLD count
   - evidence chain verified ✓ / broken ✗ / missing
3. **Exceptions:**
   - Filter by category and tier, then pick one finding.
   - You see its evidence, the **actual raw source rows** it came from, and any agent explanation.
4. **Journal & Payments:** proposed entries and the payment run. Blocked payments are highlighted red.
5. **Close report:** the rendered `close_report.md`.
6. **Human review:**
   - **Approve / Reject** each HOLD item.
   - Each click is appended to the audit log as a `human` event, logged as `HUMAN_APPROVED` / `HUMAN_REJECTED`, and the chain stays verified.

Clicking Approve/Reject on `results/demo` changes committed files. Reset it with `git checkout -- results/demo`.

---

## 5. Evaluate how good it is

### Score a run against the answer key

```bash
$C score results/<timestamp> data/try/answer_key.json --note "seed 7 baseline"
```

This prints precision, recall and F1 overall, per category and per check, and appends a row to `scoreboard.md`.

- **Precision**: how many of our flags were real problems. False flags (fp) cost points.
- **Recall**: how many planted problems we caught. Misses (fn) cost points.
- **The scorer is strict:** a second flag on an already-caught item counts as a false positive. Expect the real leaderboard to be no harsher than this.

Current baselines:

| Dataset | Command | Result |
|---|---|---|
| Normal, seed 7 | `$C synth data/s7 --seed 7` | 90 tp / 0 fp / 0 fn |
| Hard (recurring bills, boundary traps), seed 2026 | `$C synth data/h --seed 2026 --hard` | 95 / 0 / 0 |
| Messy formats (trailing minus, preambles, "Net 30", padded ids) | `pytest -k ugly` | 90 / 0 / 0 |

### Evaluate honestly — this is the part that matters

The generator was written knowing our checks, so 100% on it does **not** predict the leaderboard. To get a truer picture:

1. **Try other seeds and sizes:** `--seed 11 --n 1000`, `--seed 99 --hard`. Score each one and watch for any fp/fn.
2. **Break the mapping on purpose.** Remove one column from a copy of `config/mappings.synth.yaml`, such as `po_id`, `tax_rate` or `status`. Re-run, and read `close_report.md` notes plus the score. `RUNBOOK.md` §3 lists what each check needs and what degrades.
3. **Rehearse on scrambled columns:** `.venv/Scripts/python scripts/rehearsal_scramble.py data/try data/scrambled` renames and reorders columns (see RUNBOOK §9). Then write a fresh mapping from `$C profile data/scrambled` alone and time yourself.
4. **Tune only via `config/policy.yaml`** (tolerances, windows, materiality, tier confidence cut-offs). Each change gets its own scored run with a `--note`, so `scoreboard.md` shows what moved what.
5. **Smoke-test the agents once with a real key before the weekend:**
   - Run `$C run data/try --mapping config/mappings.synth.yaml --out results --agents`.
   - Check that the notes have no `agents unavailable` and that `close_memo.md` exists.
   - Score it and compare with the rules-only run.

---

## 6. Using it on new (real) data

1. Copy the files into `data/raw/` (git-ignored).
2. Run `$C profile data/raw > data/profile.md` and read it.
3. Copy `config/mappings.synth.yaml` to `config/mappings.real.yaml` and map each raw column to our canonical name. Options for awkward layouts are in RUNBOOK §3:
   - a list of specs per table, each with its own `defaults: {entity: DE01}`
   - `sheet`, `skip_rows`
   - `amount_from: {debit, credit}` / `sign_col`
   - `date_order: mdy`
   - `id_prefix_entity: true`
4. Run `$C run data/raw --mapping config/mappings.real.yaml --out results`. **Read the run notes first.** They tell you about skipped checks (`skipped no_po: …`), unparsed values (`invoices.invoice_date: 412/600 unparsed`) and ids repeated across entities.
5. Fix the mapping until the notes are clean, then submit. The leaderboard format is written into `src/closeops/export/event.py` on the day (RUNBOOK §4).

---

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Hundreds of `no_po` / `tax_rate` / `bank_unmatched` findings | A link column isn't mapped, or the GL has no account typed `bank`. Check the run notes and RUNBOOK §3 |
| Many payments with `status_unknown` | Invoice status column not mapped. Map it, or accept the safe HOLDs |
| `N/M unparsed` notes | Wrong `date_order`, an odd number format, or a preamble (use `skip_rows`) |
| Dashboard shows an old run | Change Results dir or click Run close. It uses the newest folder |
| Dashboard source rows empty for a file with a preamble | Known limitation: the display ignores `skip_rows`. The `file:row` refs in findings are still correct |
| `agents unavailable: …` note | No or invalid credentials (`ant auth login`), or the API rejected the request. The run continues rules-only |
| `evidence: broken` | `evidence.jsonl` was edited after the run. Re-run to get a clean log |
