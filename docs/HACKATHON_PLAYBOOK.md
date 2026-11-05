# Hackathon Playbook — from "files received" to trophy

The day-of game plan for **Pratyush + Claude Code**. Every phase has a goal, a timebox, a
**copy-paste prompt** for Claude Code and a "done when" check. Prompts assume you are in the
`nova7nov` repo. `CLAUDE.md` loads automatically, so Claude already knows the project.

> **How we win:** score well on the live leaderboard **and** on the hidden benchmark
> (generalise, don't overfit), stay safe (never auto-pay a doubtful item), and show judges a
> close they can audit click by click.

---

## 0. The night before (Friday) — 45 min

- [ ] `git pull` on the laptop you'll use. Run `.venv/Scripts/python -m pytest -q`: all green.
- [ ] `ant auth login` (or set `ANTHROPIC_API_KEY`), then run the agents smoke test (prompt A1 below). This is the one path that has never touched the real API.
- [ ] Time a full rehearsal: `closeops synth` → `scripts/rehearsal_scramble.py` → profile → write mapping → run → score (USER_GUIDE §5). Target ≤ 25 min.
- [ ] Open the dashboard once. Click through all tabs, then `git checkout -- results/demo`.
- [ ] Charger, second screen if allowed, this playbook printed or open on the second screen.

**Prompt A1 — agents smoke test**
```
Run the Claude agents end to end once against the real API and tell me if it works.
1. .venv/Scripts/closeops synth data/smoke --seed 7 --n 200
2. .venv/Scripts/closeops run data/smoke --mapping config/mappings.synth.yaml --out results/smoke --agents
3. Show me the run notes, whether close_memo.md exists and its first 20 lines, and how many
   findings have resolved_by != "rule".
4. Score it with closeops score against data/smoke/answer_key.json and compare with a rules-only
   run of the same data.
If the API rejects anything (model id, betas, fallbacks, output_config), find the cause from the
error text, fix agents/team.py test-first, and re-run. Do not change any check logic.
```

---

## Roles on the day

| Pratyush (domain + driver) | Claude Code (builder) |
|---|---|
| Reads the brief and scoring rules aloud, owns the submission button | Profiles data, writes the mapping, exporter, fixes, tests |
| Sanity-checks the mapping and findings like an accountant | Runs and scores everything, keeps the suite green, commits |
| Watches the leaderboard and decides what to chase | Explains each false positive/negative with evidence |
| Rehearses and gives the demo | Prepares demo run, numbers, fallback |

Rule of thumb: **Pratyush decides *what*, Claude does *how*.** Never both stuck on the same bug for more than 10 minutes. Timebox, log it, move on.

---

## Phase 1 — Brief & scoring (0:00–0:15)

**Goal:** know exactly what is scored and in which file format.

**Prompt P1 — read the brief**
```
The challenge materials are in <PATH_TO_BRIEF_FILES>. Read all of them (PDF/MD/README/sample
files). Write docs/day/brief.md containing:
1. The task in 5 bullet points.
2. The submission format EXACTLY: file name(s), columns/fields, types, id formats, allowed
   category/label values, encoding, how many files, how to submit, rate limits on submissions.
3. How each leaderboard dimension (accounting accuracy, reconciliation, fraud detection, safe
   autonomy, auditability) is computed — quote the source. If something is unclear, list it
   under "Questions for organisers".
4. A mapping table: their categories/labels → our checks (closeops.checks.*) and our tiers
   (AUTO/AUTO_NOTE/HOLD). Mark anything they score that we don't produce yet.
5. Any rules we must respect (e.g. human-in-the-loop requirements, thresholds, approval limits,
   chart of accounts, tax rates, period dates) and which config/policy.yaml keys they change.
Do not write code yet.
```
**Done when:** `docs/day/brief.md` exists, and Pratyush has asked the organisers the open questions.

---

## Phase 2 — Data intake & mapping (0:15–0:45)

**Goal:** the real data loads cleanly into our canonical tables.

Copy the dataset into `data/raw/` (git-ignored). Don't rename files.

**Prompt P2 — profile and map**
```
New real dataset is in data/raw/. Follow RUNBOOK.md §3 (mapping checklist).
1. Run .venv/Scripts/closeops profile data/raw > data/profile.md and summarise it: one line per
   file with what table it is (invoices, purchase_orders, goods_receipts, contracts, vendors,
   vendor_bank_history, bank_transactions, gl_entries, accounts, fx_rates, tax_rates) or
   "unknown/ignore", entity coverage, date format (dmy/mdy), number format, preambles,
   debit/credit vs signed amounts, per-entity files without an entity column, ids that repeat
   across entities, and any PDFs.
2. Write config/mappings.real.yaml from config/mappings.synth.yaml as the template, using the
   mapping options in RUNBOOK §3 where needed (list specs with per-file defaults, sheet,
   skip_rows, amount_from / sign_col, date_order, id_prefix_entity). Set period_end from the brief.
3. Run .venv/Scripts/closeops run data/raw --mapping config/mappings.real.yaml --out results
   and show me ALL run notes (skipped checks, "N/M unparsed", repeated ids, errors).
4. Iterate on the mapping until notes show no unparsed values in key columns and no
   unexpected skipped checks. Then show me: row counts per canonical table, findings per
   check, tiers, % bank lines reconciled, trial balance balanced per entity.
5. List canonical columns we could NOT map and which checks that weakens (RUNBOOK §3 table).
Do not change check code in this phase. Commit the mapping.
```
**Prompt P2b — only if there are PDF invoices**
```
data/raw contains PDF invoices. Write an adapter test-first: pdfplumber text extraction →
Claude structured extraction (Messages API, claude-sonnet-5-5, strict tool schema with the
canonical invoices columns) → a CSV in data/raw/_extracted/ that the mapping can load as an
extra invoices spec, with source_file = the PDF name. Cache extractions on disk so re-runs are
free. Flag low-confidence extractions in a column. Run it, then re-run the close and compare
invoice counts.
```
**Done when:** the run notes are clean, and the numbers look plausible to Pratyush. Spot-check 5 findings in the dashboard Exceptions tab, looking at the raw source rows.

---

## Phase 3 — Exporter & FIRST SUBMISSION (0:45–1:30)

**Goal:** a valid leaderboard submission. Rules-only, no agents.

**Prompt P3 — event exporter**
```
Implement the leaderboard exporter per RUNBOOK §4 and docs/day/brief.md "submission format".
Test-first: tests/export/test_event.py runs run_close on generate(tmp, seed=7, n_invoices=200)
and asserts exact file names, columns/order, label values, id formats (source ids — strip the
"<entity>:" prefix if id_prefix_entity is on and the format wants raw ids), one row per scored
item, soft checks excluded. Copy any sample file from the brief into tests/export/fixtures/
and assert our output has the same header. Implement src/closeops/export/event.py with ONE
mapping dict at the top (our category/check/tier → their labels). Add a CLI command
`closeops export RUN_DIR --out submissions/` that calls it. Then run it on the latest real run,
show me the first 5 rows and the row count, and validate with the official validator if one
exists. Commit.
```
Then **submit**, and log it:
```
.venv/Scripts/closeops score <run> <key-if-any> --note "sub#1 rules-only baseline"   # if no key: add the leaderboard score to scoreboard.md by hand
```
**Done when:** a leaderboard score is on the board, with a matching line in `scoreboard.md`.

---

## Phase 4 — Improve loop (1:30–4:00) — where the trophy is won

Repeat in 20–30 min cycles: **look → explain → fix the biggest lever → re-run → submit → log.**

**Prompt P4 — find the biggest lever**
```
Latest run: <RUN_DIR>. Leaderboard feedback: <PASTE SCORES / ANY PER-DIMENSION FEEDBACK>.
Analyse where we lose points, per dimension:
- accuracy/fraud: which checks produce the most findings, and for the 3 biggest, sample 10
  findings each with their raw source rows and say if each looks like a true or false positive
  (reason like an accountant);
- reconciliation: unmatched bank lines — group them by pattern (fees, FX, partial payments,
  batch payments, references we don't parse, missing GL);
- safe autonomy: count AUTO/AUTO_NOTE/HOLD; list HOLDs that look unnecessary and AUTOs that
  look risky;
- auditability: are explanations, source_refs and evidence complete for every exported row?
Rank the top 5 fixes by (expected points × confidence) / minutes. For each: is it a mapping
fix, a policy.yaml change, or a code change? Don't change anything yet.
```
**Prompt P4a — policy change (cheapest)**
```
Change config/policy.yaml: <KEY> from <A> to <B> because <REASON FROM DATA/BRIEF>.
Re-run on data/raw, export, and show the diff in findings per check and tiers vs the previous
run. Also re-run on synthetic seed 7 and hard seed 2026 and confirm we don't lose recall there
(guard against overfitting). Commit with a message stating the reason.
```
**Prompt P4b — fix a check, or add a new pattern (code)**
```
Real-data pattern: <DESCRIBE, e.g. "bank fees booked as separate lines make payments 1–15 EUR
short", or "vendors re-issue invoices with suffix -R">. Example rows: <file:row refs>.
Fix it test-first:
1. Reproduce the pattern in a unit test with tests/helpers.py (anonymised values; no real ids).
2. Implement the smallest general rule in the right checks/*.py (thresholds in policy.yaml,
   never keyed to specific ids/values). Keep one-finding-per-record precedence.
3. If it is a new trap type, add it to synth/generate.py hard mode + answer key so we can
   measure it, keeping default generator output byte-identical.
4. Full suite green; seed 7 still 90/0/0; hard 2026 not worse. Re-run real data and show the
   change. Commit.
```
**Prompt P4c — enable the agents (once rules-only is solid)**
```
Run the real data with --agents (agents.max_residuals / wall_clock_seconds as in policy).
Compare against the last rules-only run: findings changed (dismissed / confirmed / escalated),
tiers changed, and spot-check 5 agent decisions with their explanations against the raw rows.
Recommend whether to submit the agent run. Only recommend it if it plausibly scores ≥ rules-only.
```
**Leaderboard lever cheat-sheet**

| Dimension | Usual levers |
|---|---|
| Accounting accuracy | Correct mapping (signs, dates, tax rates, period_end), JE account map (`accounts.*`), cut-off, VAT recalculation |
| Reconciliation | Bank fees/partial payments, batch payments (`bankrec.max_group`), reference parsing, FX tolerance, GL bank-account typing |
| Fraud detection | IBAN-change window, unknown IBAN, shared IBAN, split invoices vs real approval limit, duplicates (exact/near/cross-entity/payment) |
| Safe autonomy | Correct HOLDs (bank changes, high risk, materiality) without over-holding: tune `materiality_eur`, `tiers.*`; never weaken the IBAN rule |
| Auditability | Every exported row has explanation + `source_refs`; evidence chain verified; `policy_used.yaml`; export file hashes in the log |

**Discipline:** one change per submission, always with a `--note`. If a submission scores worse, revert that commit (`git revert`) and log why.

---

## Phase 5 — Hidden-benchmark proofing (4:00–5:00)

The final is decided on data we never see, so this phase is about generalising.

**Prompt P5 — generalisation check**
```
Audit for overfitting to the visible dataset:
1. grep the code and config for any literal ids, vendor names, amounts or dates that came from
   data/raw — remove them.
2. Re-run synthetic seeds 7, 11, 99 (normal) and 2026 (hard), plus the ugly-data test; report
   scores vs this morning's baselines.
3. Re-run real data with policy thresholds ±20% for the 3 most sensitive keys; report how
   stable findings are. Recommend the most robust setting, not the best visible-leaderboard one.
4. Confirm: full suite green, rules-only path works, evidence chain verifies, the run finishes
   in < 60 s without agents.
```

---

## Phase 6 — Freeze & demo prep (5:00–end)

**Feature freeze.** Only fixes for crashes from here on.

**Prompt P6 — demo package**
```
Prepare the demo:
1. Run the final config on the real data (rules-only and with --agents) into results/final/.
   Verify evidence chains.
2. Pick 3 "wow" findings for the story: one duplicate, one tax error, one payment blocked
   because of a changed IBAN. Give their finding ids, the dashboard clicks to reach each, the
   raw source rows, the JE or blocked payment, and one sentence each in plain finance language.
3. Write docs/day/pitch.md: 3-minute script (problem → live run → the 3 findings → human
   approves one HOLD → audit trail verified → numbers: findings, % reconciled, AUTO vs HOLD,
   leaderboard position), plus 5 likely judge questions with crisp answers (safety, hidden
   benchmark generalisation, why rules + agents, auditability, what we'd build next).
4. Copy the final run to results/demo (and its raw data if allowed) so the fallback works offline.
```
**Done when:** you have rehearsed the demo twice, run `git checkout -- results/demo` before going on stage, and made the last submission.

---

## Emergency prompts

**Run crashes or produces nothing**
```
closeops run on data/raw fails: <PASTE ERROR / NOTES>. Find the root cause (don't guess),
reproduce it in a failing test with tests/helpers.py, fix it minimally so the stage degrades
to a run note instead of crashing, full suite green, re-run, commit.
```
**Score dropped after a change**
```
Submission <N> scored <X> vs <Y> before. Diff the two runs' findings.csv by check and tier,
show what changed and why, and tell me whether to revert. If yes, git revert that commit,
re-run, export, and log it in scoreboard.md.
```
**Out of time**
```
We have <M> minutes. Stop all feature work. Make sure main is green, produce the final export
from the best-scoring commit in scoreboard.md, verify the file once by hand (first 5 rows,
row count), and prepare the demo fallback from results/demo.
```

---

## Pitch messages for the judges (keep these in your head)

1. **Safe by design:** money never goes to changed bank details without a human. That is a hard rule in code, enforced at three layers.
2. **Traceable:** every number links to its raw file and row, and the audit log is hash-chained and verifiable live.
3. **Rules where certainty is possible, agents where judgment is needed:** deterministic checks do the arithmetic, Claude agents review the borderline cases and never touch amounts.
4. **Built to generalise:** no dataset-specific hacks, thresholds in one policy file, measured on harder synthetic data. That is why we expect to hold up on the hidden benchmark.
5. **Autonomy with honesty:** AUTO / AUTO_NOTE / HOLD tiers show exactly what was done alone and what needs a person.
