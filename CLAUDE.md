# closeops — autonomous finance close (hackathon project)

Talky × UCppM Claude Code Build Day: "Build an AI finance team, then let it close the books."
We get an unseen synthetic multi-entity company (invoices, POs, receipts, contracts, vendors,
bank transactions, GL, ERP history). A live leaderboard scores **accounting accuracy,
reconciliation, fraud detection, safe autonomy, auditability**; a **hidden benchmark** decides
the final. Named traps: duplicates, tax errors, suspicious bank-detail changes.

Read `docs/USER_GUIDE.md` (how to run/evaluate), `RUNBOOK.md` (day-of checklist, mapping
checklist, policy knobs, known weaknesses) and `docs/HACKATHON_PLAYBOOK.md` (phase prompts).

## Commands
- Tests: `.venv/Scripts/python -m pytest -q` (all must stay green; ~290 tests, ~25 s)
- CLI: `.venv/Scripts/closeops {profile RAW | synth OUT [--seed --n --hard] | run RAW --mapping M [--policy P] [--out results] [--agents] | score RUN KEY [--note]}`
- Dashboard: `.venv/Scripts/streamlit run dashboard/app.py`
- Python 3.12 venv in `.venv` (system `python` is 3.11 — never use it). Windows; use pathlib.

## Pipeline (src/closeops)
raw files → `ingest/mapping.py load_dataset` (YAML mapping → canonical tables in `model.CANONICAL`,
every row keeps `source_file`/`source_row`; unparsed counts → `Dataset.notes`)
→ `checks/registry.run_checks` (duplicates, tax, fraud, purchasing, bankrec, gl; each guarded)
→ `policy.assign_tiers` (AUTO / AUTO_NOTE / HOLD) → optional `agents/team.resolve_residuals`
→ `journal.propose_journal_entries` + `propose_payments` → `export/generic.write_results`
(+ `export/event.py` = leaderboard format, written on the day) → `evidence.EvidenceLog` (hash chain).
`pipeline.run_close` wires it; `scoring.score` compares to an answer key; `synth/generate.py` makes practice data.

## Rules that must not be broken
- **No payment to a changed/unverified IBAN is ever AUTO or AUTO_NOTE** (journal `_iban_changed_vendors`, `policy._tier`, agents may not dismiss `iban_*`/`shared_iban`).
- Checks never call an LLM; agents never compute amounts (they call `agents/tools.py`).
- No logic keyed to specific record ids or values from any dataset. Thresholds live in `config/policy.yaml`.
- Finding conventions: one finding per (category, primary record) with `evidence.also_triggered`; `record_ids[0]` is the primary id (duplicates: the LATER copy; split_invoices: latest invoice); `entity_pair`/`vendor` findings are keyed by `"|".join(sorted(record_ids))` in the scorer. `evidence` holds plain JSON values only.
- Soft signals (`checks.fraud.SOFT_CHECKS`) are never scored/exported unless policy says so.
- Every pipeline stage is guarded: a failure becomes a run note, never a crash with no output.
- TDD: write the failing test first, then the code. Run the full suite before every commit.
- Commit after every green change (small commits; message + `Co-Authored-By` trailer). Never commit `data/` (raw event data) — it is git-ignored.

## Hackathon working agreements
- Real data lives in `data/raw/` (ignored). Its mapping is `config/mappings.real.yaml`.
- Every leaderboard submission gets a scored/logged line in `scoreboard.md` with a note of what changed.
- Prefer fixing the mapping over changing check code; prefer policy changes over code changes; change check code only with a test that reproduces the real-data case (anonymised, built with `tests/helpers.py`).
- Keep `use_agents=False` (rules-only) as the safe submission baseline; only submit agent runs that score ≥ rules-only.
