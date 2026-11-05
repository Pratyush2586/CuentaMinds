# Autonomous Finance Office — Design (Talky × UCppM Claude Code Build Day)

Date: 2026-11-05 · Team: Pratyush + Claude (Claude Code) · Event: weekend of 2026-11-07

## 1. Intent

Build an "AI finance team" that takes a synthetic multi-entity company's raw records
(invoices, POs, bank transactions, contracts, vendor master, general ledger, ERP history)
and runs a **safe, traceable, audit-ready month-end close** with minimal human input.

**Why / what winning means (user's words):** top the live leaderboard *and* impress the
judges with the demo. The real dataset is only shared on the day, so everything must be
pre-built so the shared input "can be easily fit to start work quick".

Leaderboard dimensions (from the brief): **accounting accuracy, reconciliation, fraud
detection, safe autonomy, auditability**. A **hidden benchmark** decides the final. Known
traps: **duplicates, tax errors, suspicious bank changes**. Expect ~15+ teams, ~100 people.

## 2. Goals, non-goals, anti-goals

**Goals**
1. A first valid leaderboard submission within ~90 minutes of receiving the data.
2. High precision *and* recall on the named traps (duplicates, tax, bank-detail changes)
   plus the usual close checks (3-way match, bank rec, intercompany, cut-off).
3. Every output line traceable to source evidence, the rule/agent that decided it, and
   a confidence + autonomy decision.
4. A live, clickable demo: the close runs end-to-end, exceptions surface, a judge can
   drill from any journal entry to its evidence.
5. Generalise to the hidden benchmark — no logic hard-coded to visible record IDs.

**Non-goals**
- A real ERP integration, auth, multi-user, or production hardening.
- Pretty UI beyond what the demo needs.
- Training/fine-tuning models.

**Anti-goals (failure even if it "works")**
- Auto-approving or auto-paying anything touching a changed bank account.
- LLM-only decisions on numbers the code can compute exactly (amounts, VAT, matches).
- Overfitting the visible leaderboard (e.g. tuning thresholds to specific rows) so the
  hidden benchmark drops us.
- A demo that is a slideshow rather than the system running.
- Losing time on the day to schema wrangling we could have prepared for.

## 3. Constraints

- One build day, two builders (user + Claude in Claude Code). User's machine is Windows 11.
- Data format unknown until the day (likely CSV/XLSX/JSON; possibly PDF invoices).
- Submission format unknown. **Decision (Claude's call):** assume uploaded output files.
  The system produces one canonical result set; a thin exporter maps it to the
  announced format. If it turns out to be an API or judges running code, only the
  exporter/entrypoint changes.
- Claude API access via the event / user's account. Must stay fast: whole pipeline
  re-runs in minutes so we can iterate against the leaderboard.

## 4. Architecture

```
 raw files ──► [1 Ingest adapters] ──► canonical store (DuckDB/pandas)
                                            │
                    ┌───────────────────────┼─────────────────────────┐
                    ▼                       ▼                         ▼
          [2 Rule engine checks]   [3 Agent team (Claude)]   [4 Policy / autonomy gate]
          deterministic, tested    ambiguous cases, explain   auto / auto+note / hold
                    └──────────────┬────────┴─────────────────────────┘
                                   ▼
                         [5 Evidence log (append-only, hash-chained)]
                                   ▼
            [6 Outputs] submission exporter · proposed journal entries ·
                        exception queue · close report · audit pack
                                   ▼
                         [7 Demo dashboard]
```

Language: **Python 3.12** (Claude's call: best data tooling — pandas/DuckDB, rapidfuzz,
pdfplumber, openpyxl, Claude Agent SDK). Dashboard: **Streamlit** (Claude's call: fastest
path to a live, clickable demo with zero frontend build).

### 4.1 Ingest adapters ("fit the input fast")
- Auto-profile every file on arrival: columns, types, sample rows, null rates, key
  candidates → `profile.md` we read in the first 10 minutes.
- **Schema mapping by config, not code:** `mappings.yaml` maps source columns →
  canonical fields. Claude drafts the YAML from the profile; human checks it.
- Normalisers: dates (multi-format), amounts (comma/dot decimals, currency symbols,
  negatives in parentheses), IBAN/BIC cleanup, VAT IDs, vendor-name normalisation.
- PDF invoices (if any): pdfplumber text → Claude structured extraction → same schema,
  flagged as extracted-with-confidence.

### 4.2 Canonical model
Entities: `Entity`, `Vendor` (+ `VendorBankHistory`), `Customer`, `PurchaseOrder`,
`GoodsReceipt`, `Invoice` (+ lines), `Contract` (terms, prices, payment terms),
`BankTransaction`, `GLEntry`, `Account` (chart of accounts), `FXRate`, `TaxRate`.
Every row keeps `source_file`, `source_row` for traceability.

### 4.3 Rule engine — check catalogue
Each check is a pure function → list of `Finding(type, severity, records, evidence,
confidence, suggested_action)`. Unit-tested against a seeded synthetic dataset.

| Area | Checks |
|---|---|
| Duplicates | exact dup (vendor+invoice no.+amount); near-dup (normalised invoice no., same amount ±date window, fuzzy vendor); same invoice across entities; dup bank payments |
| Tax | recompute VAT per line by country/rate; wrong rate; math errors (net+tax≠gross); missing VAT ID; EU reverse-charge/intra-community misapplied; rounding drift |
| Fraud / bank changes | vendor IBAN changed shortly before a payment; payment to IBAN not in vendor master; bank country ≠ vendor country; new vendor + large/urgent payment; split invoices just under approval limits; round amounts; weekend/holiday postings; vendor/employee bank overlap |
| Purchasing | 3-way match PO↔receipt↔invoice with tolerances; invoice without PO; price ≠ contract price; qty billed > received |
| Bank reconciliation | 1:1 exact, amount+date window, 1:many / many:1 (subset-sum, bounded), FX-converted matching; unreconciled residuals listed |
| GL / close | trial balance balances per entity; intercompany mirror mismatch + eliminations; cut-off (invoice/service date vs period) → accruals/deferrals; FX revaluation; suspense account clearance |

### 4.4 Agent team (Claude Agent SDK, Python)
Agents get **the rule engine as tools** (query records, run a check, fetch evidence);
they never compute amounts themselves.

- **AP Clerk** — resolves near-duplicate and 3-way-match ambiguities.
- **Reconciler** — explains unmatched bank lines, proposes matches beyond rules.
- **Tax Reviewer** — judges edge cases (reverse charge, exemptions) with cited rule.
- **Fraud Analyst** — scores suspicious patterns, writes the risk narrative.
- **Controller** — reviews proposed JEs + open exceptions, decides close readiness,
  writes the close memo. Has veto.

Models: Sonnet 5.5 for bulk triage, Opus 5.5 for Controller and hard cases (Claude's
call; swap if rate limits bite). Prompt caching on the shared context. Agents run only
on findings the rules couldn't settle — keeps runs fast and cheap.

### 4.5 Safe autonomy policy
Every proposed action gets a tier:
- **AUTO** — deterministic, high confidence, below materiality → posted.
- **AUTO + NOTE** — posted, flagged for after-the-fact review.
- **HOLD** — needs a human: any bank-detail change, fraud score ≥ threshold, amount ≥
  materiality, agent/rule disagreement, low-confidence extraction.

Hard rules: no payment release to changed/unverified bank details; segregation of duties
(the agent that proposes can't approve); dry-run mode; one-switch kill. The policy lives
in `policy.yaml` so it's visible to judges and tunable for the leaderboard.

Leaderboard tactic: "safe autonomy" likely rewards *correct* auto-handling and penalises
both wrong autos and needless holds — tune thresholds on our seeded data, not the
leaderboard rows.

### 4.6 Evidence log (auditability)
Append-only JSONL, each event: `id, ts, actor (rule|agent|human), action, inputs
(record refs), evidence, output, confidence, tier, prev_hash, hash`. Hash chain makes
tampering evident. Whole run is replayable from raw files + config + log. Audit pack
export: close memo, JE list with evidence links, exception register, policy used.

### 4.7 Outputs & submission
Canonical `results/` (findings, JEs, reconciliations, holds) → `exporters/<format>.py`.
Write the generic CSV/JSON exporter beforehand; write the event-specific one on the day.

### 4.8 Demo dashboard (Streamlit)
1. "Run close" button → live pipeline progress per stage and per agent.
2. Exceptions board grouped by type with severity and tier.
3. Drill-down: JE / finding → evidence → source rows → agent reasoning.
4. Close report: per-entity status, TB balanced, reconciled %, holds awaiting human.
5. Human-in-the-loop: approve/reject a HOLD live and watch the close update.

Demo script (3 min): messy data in → run → it catches the planted duplicate, wrong VAT
and changed-IBAN payment → shows why it *held* the IBAN one → click-through audit trail
→ close memo signed off by the Controller with N items for human review.

## 5. Plan of attack

**Priority if prep runs short (agreed with Pratyush): leaderboard over demo polish.**
Cut from the bottom of this order: (1) ingest + rule checks, (2) synthetic dataset +
local scorer, (3) exporter, (4) basic dashboard, (5) agents on residuals. Within the
checks, the named traps (duplicates, tax, bank-detail changes) come first, then bank
rec and 3-way match, then GL/close checks.

**Before the weekend (Thu–Fri)**
1. Repo skeleton, canonical model, ingest + profiler + YAML mapping.
2. **Synthetic rehearsal dataset** generator: 3 entities, multi-currency, with planted
   traps of every type in §4.3 and a ground-truth answer key → our own local scorer.
3. Rule engine checks + tests against the answer key.
4. Agent team + policy gate + evidence log.
5. Streamlit dashboard + demo script.
6. Full dry run: fresh "unseen" synthetic dataset → first output in < 30 min.

**Build day runbook**
- 0:00–0:30 profile data, read brief/scoring, draft mappings.yaml, write exporter.
- 0:30–1:30 ingest + rules → **first leaderboard submission** (rules only).
- 1:30–4:00 iterate per leaderboard dimension; turn on agents for residuals; log each
  submission + score in `scoreboard.md` to see what moved what.
- 4:00–5:30 data-specific checks discovered on the day; tune policy (on rules, not rows).
- 5:30–end freeze features, rehearse demo twice, final submission.

Roles: **Pratyush** — data inspection, mapping sign-off, leaderboard submissions,
domain sanity checks, demo presenter. **Claude** — code, tests, fixes, agents, exporter.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Unexpected file formats | profiler + YAML mapping + Claude-drafted adapters |
| API rate limits / slowness | agents only on residuals, caching, rules-only fallback path |
| Overfitting visible leaderboard | thresholds justified by accounting logic; local scorer on our own data |
| Unknown accounting conventions (country tax rules) | tax rates as config table; Tax Reviewer cites rule |
| Demo breaks live | recorded fallback run + pre-computed results cached |

## 7. Decisions made without the scoring rules (Claude's calls — revisit on the day)

1. **What gets scored is unknown, so canonical results hold both.** (a) Labelled
   detections per record (duplicate / tax error / fraud-risk / match status) and
   (b) proposed journal entries + per-entity adjusted trial balance. Build order:
   detection labels first (most likely scored, and the named traps), then a *simple*
   posting engine (JE = lines with account, entity, amount, currency, reason, evidence
   ref). Accruals and FX revaluation come after that.
2. **Per-entity close first.** Group consolidation (intercompany eliminations,
   translation into a group currency taken from the data, closing rate for the balance
   sheet and average rate for P&L, rates from the dataset's FX table) is a stretch goal. The
   canonical model carries `entity` and `currency` on every row from day one, so adding
   it needs no schema rework.
3. **Payments: both views.** Past bank transactions get after-the-fact flags. Open,
   approved AP invoices get a **payment proposal** list, and the IBAN-change rule
   *blocks* a proposed payment (tier HOLD). This keeps the "no payment to changed bank
   details" rule real, and it's a strong demo moment.
4. **HOLD in a batch export** = the record is still reported as detected/flagged, with
   an extra `autonomy_tier` + `requires_human_review` field. Default for unclear fraud
   cases is flag + HOLD. Default for unclear non-fraud cases is AUTO + NOTE. Retune once
   the scoring is known.
5. **Claude stays in the scored path, with a fallback.** Agents only work on residuals
   the rules can't settle. If an agent run scores lower than rules-only on our local
   scorer or the leaderboard, we submit the better one and log why. The brief is "build
   an AI finance team", so the judges will expect agents to be visibly doing work.

## 8. Left to the builder
Internal module layout, test framework choice (pytest assumed), exact fuzzy thresholds,
subset-sum bounds, dashboard styling, synthetic data generator details.
