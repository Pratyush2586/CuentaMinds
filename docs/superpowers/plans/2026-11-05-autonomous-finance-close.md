# Autonomous Finance Close — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python system that ingests a multi-entity company's raw finance files, detects duplicates / tax errors / fraud signals, reconciles bank, PO and GL data, proposes journal entries and payments under a safe-autonomy policy, logs every decision to a tamper-evident evidence log, exports leaderboard-ready files, and shows it all in a Streamlit demo.

**Architecture:** Config-driven ingest (`mappings.yaml`) → canonical pandas tables (`Dataset`) → deterministic checks producing `Finding`s → policy assigns autonomy tier → Claude agents resolve only mid-confidence residuals → journal/payment proposals → evidence log + exporters → Streamlit dashboard. A synthetic generator with an answer key and a local scorer lets us measure everything before the real data arrives.

**Tech Stack:** Python 3.12, pandas, rapidfuzz, PyYAML, openpyxl, pdfplumber, streamlit, anthropic / claude-agent-sdk, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-autonomous-finance-close-design.md`

## Global Constraints

- Python 3.12, src layout, package name `closeops`, CLI entry `closeops`.
- Windows 11 host: use `pathlib` everywhere, run tests with `python -m pytest`, read/write files as UTF-8.
- Checks never call an LLM. Agents never compute amounts; they call check/query tools.
- Models: `claude-sonnet-5-5` for bulk triage, `claude-opus-5-5` for the Controller.
- Every canonical row carries `source_file` and `source_row`; every `Finding` carries record ids that trace back to them.
- No logic keyed to specific record ids/values from any dataset; thresholds live in `config/policy.yaml`.
- Hard rule: no payment proposal to a changed/unverified IBAN is ever tier `AUTO` or `AUTO_NOTE`.
- Priority if time runs short (cut from the bottom): ingest + checks → synthetic data + scorer → exporter → dashboard → agents.
- Full pipeline on the synthetic dataset runs in under 60 s without agents.
- The system only *proposes* (JEs, payments); nothing is posted to an external system, so every run is a dry run. `--agents` off is the kill switch back to rules-only.
- PDF invoice extraction is deferred: `profile_dir` counts PDFs; if the real data has them, a `pdfplumber` → Claude structured-extraction adapter is written on the day (RUNBOOK checklist item).

## Review Focus

1. European number formats (`1.234,56`, `(500.00)`, `€ 1 200`) → parsed to the right float (Task 2).
2. A dataset missing optional tables (no contracts, no goods receipts) → dependent checks skip with a note, pipeline completes (Tasks 3, 10).
3. Same invoice number from two *different* vendors → not a duplicate (Task 4).
4. Credit notes (negative net) → not flagged as a tax error and not a duplicate of the invoice they credit (Tasks 4, 5).
5. Multi-currency: EUR invoice paid by USD bank line → reconciled via FX, not left unmatched (Task 8).

---

## File Structure

```
pyproject.toml
config/policy.yaml                 thresholds, materiality, tiers, account map
config/mappings.synth.yaml         mapping for our synthetic dataset (template for the day)
src/closeops/
  model.py                         CANONICAL columns, Dataset, Finding, Tier
  ingest/normalize.py              parse_amount, parse_date, clean_iban, norm_vendor, norm_invoice_no
  ingest/profile.py                profile_dir -> markdown
  ingest/mapping.py                load_dataset(raw_dir, mapping_path) -> Dataset
  checks/duplicates.py             duplicate invoices / payments
  checks/tax.py                    VAT recompute, rate, reverse charge
  checks/fraud.py                  IBAN changes, unknown IBAN, splits, soft signals
  checks/purchasing.py             3-way match, contract price
  checks/bankrec.py                bank reconciliation
  checks/gl.py                     TB balance, intercompany, cut-off
  checks/registry.py               run_checks(ds, policy) -> list[Finding]
  policy.py                        load_policy, assign_tiers
  evidence.py                      EvidenceLog (hash chain)
  journal.py                       propose_journal_entries, propose_payments
  synth/generate.py                generate(out_dir, seed) -> writes raw files + answer_key.json
  scoring.py                       score(findings, answer_key)
  export/generic.py                write_results(run, out_dir)
  export/event.py                  stub; written on the day for the announced format
  pipeline.py                      run_close(...) -> RunResult
  agents/tools.py                  tool functions exposed to agents
  agents/team.py                   resolve_residuals(...)
  cli.py                           closeops profile|synth|run|score
dashboard/app.py                   Streamlit demo
tests/...                          one test module per src module
RUNBOOK.md                         build-day runbook
```

---

### Task 1: Scaffold + canonical model

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/closeops/__init__.py`, `src/closeops/model.py`, `tests/conftest.py`, `tests/test_model.py`

**Interfaces:**
- Produces:
  - `CANONICAL: dict[str, list[str]]` — exact tables/columns:
    - `vendors`: vendor_id, entity, name, country, vat_id, iban
    - `vendor_bank_history`: vendor_id, iban, changed_at, changed_by
    - `purchase_orders`: po_id, entity, vendor_id, po_date, item, qty, unit_price, currency, approved_by
    - `goods_receipts`: gr_id, po_id, receipt_date, qty_received
    - `invoices`: invoice_id, entity, vendor_id, invoice_no, invoice_date, service_period_end, po_id, item, qty, unit_price, currency, net_amount, tax_rate, tax_amount, gross_amount, vendor_country, buyer_country, status
    - `contracts`: contract_id, vendor_id, item, unit_price, currency, valid_from, valid_to, payment_terms_days
    - `bank_transactions`: txn_id, entity, txn_date, amount, currency, counterparty_name, counterparty_iban, reference
    - `gl_entries`: je_id, entity, posting_date, account, debit, credit, currency, description, counterparty_entity
    - `accounts`: account, name, type
    - `fx_rates`: date, currency, rate_to_eur
    - `tax_rates`: country, rate, kind
    - every table additionally has `source_file`, `source_row`
  - `class Dataset` with `tables: dict[str, pd.DataFrame]`, `period_end: date`, `has(name) -> bool`, `table(name) -> pd.DataFrame` (empty frame with canonical columns if absent)
  - `class Tier(str, Enum)`: `AUTO`, `AUTO_NOTE`, `HOLD`
  - `@dataclass Finding`: check: str, category: str (`duplicate|tax|fraud|match|bankrec|gl`), severity: str (`low|medium|high`), entity: str, record_type: str, record_ids: list[str], evidence: dict, confidence: float, suggested_action: str, amount_eur: float = 0.0, tier: Tier | None = None, requires_human_review: bool = False, explanation: str = "", resolved_by: str = "rule"; property `finding_id -> f"{check}:{'|'.join(record_ids)}"`; `to_dict() -> dict` (JSON-safe, tier as string)
  - `tests/conftest.py` fixture `make_ds(period_end="2026-10-31", **tables: list[dict]) -> Dataset`, filling missing canonical columns with NA and `source_file="test"`, `source_row=index`

- [ ] **Step 1:** Write `pyproject.toml` (deps: pandas, rapidfuzz, pyyaml, openpyxl, pdfplumber, streamlit, anthropic, claude-agent-sdk; dev: pytest; script `closeops = "closeops.cli:main"`), `.gitignore` (`.venv/`, `results/`, `data/raw/`, `__pycache__/`). Run `python -m venv .venv; .venv\Scripts\pip install -e .[dev]`. Expected: install succeeds.
- [ ] **Step 2: Write failing tests**

```python
def test_finding_id_and_dict():
    f = Finding(check="dup_exact", category="duplicate", severity="high", entity="E1",
                record_type="invoice", record_ids=["INV-2", "INV-1"], evidence={"x": 1},
                confidence=0.99, suggested_action="reverse", tier=Tier.HOLD)
    assert f.finding_id == "dup_exact:INV-2|INV-1"
    assert f.to_dict()["tier"] == "HOLD"

def test_missing_table_is_empty_with_columns(make_ds):
    ds = make_ds(invoices=[{"invoice_id": "I1"}])
    assert not ds.has("contracts")
    assert list(ds.table("contracts").columns) == CANONICAL["contracts"] + ["source_file", "source_row"]
```

- [ ] **Step 3:** Run `python -m pytest tests/test_model.py -v` → FAIL (import error).
- [ ] **Step 4:** Implement `model.py` and the `make_ds` fixture.
- [ ] **Step 5:** Run → PASS. Commit `feat: scaffold and canonical model`.

---

### Task 2: Normalisers

**Files:** Create `src/closeops/ingest/normalize.py`, `tests/ingest/test_normalize.py`

**Interfaces:**
- Produces: `parse_amount(v) -> float | None`, `parse_date(v) -> date | None`, `clean_iban(v) -> str | None` (uppercase, no spaces), `norm_vendor(name) -> str` (casefold, strip punctuation and legal suffixes `sl, sa, gmbh, ltd, inc, bv, srl, sas, llc`), `norm_invoice_no(v) -> str` (uppercase, drop non-alphanumerics, strip leading zeros of the trailing digit run)

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize("raw,exp", [("1.234,56", 1234.56), ("1,234.56", 1234.56), ("(500.00)", -500.0),
    ("€ 1 200", 1200.0), ("-75", -75.0), ("", None), (None, None), (12.5, 12.5)])
def test_parse_amount(raw, exp): assert parse_amount(raw) == exp

@pytest.mark.parametrize("raw", ["2026-10-03", "03/10/2026", "03.10.2026", "2026/10/03"])
def test_parse_date_day_first(raw): assert parse_date(raw) == date(2026, 10, 3)

def test_clean_iban(): assert clean_iban(" es91 2100 0418 4502 0005 1332 ") == "ES9121000418450200051332"
def test_norm_vendor(): assert norm_vendor("ACME, S.L.") == norm_vendor("Acme SL") == "acme"
def test_norm_invoice_no(): assert norm_invoice_no("INV-00123") == norm_invoice_no("inv 123") == "INV123"
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (amount rule: if both `.` and `,` present, the last one is the decimal separator; a lone `,` followed by exactly 2 digits is decimal; dates are day-first). **Step 4:** Run → PASS. **Step 5:** Commit `feat: value normalisers`.

---

### Task 3: Profiler + mapping loader

**Files:** Create `src/closeops/ingest/profile.py`, `src/closeops/ingest/mapping.py`, `tests/ingest/test_mapping.py`, `tests/fixtures/raw_small/` (two tiny CSVs + one XLSX written by the test itself via `tmp_path`)

**Interfaces:**
- Consumes: normalisers (Task 2), `Dataset`, `CANONICAL` (Task 1)
- Produces:
  - `profile_dir(raw_dir: Path) -> str` — markdown: per file (csv/xlsx sheets/json/pdf count) columns, dtype guess, null %, 3 sample rows, candidate key columns (100 % unique)
  - `load_dataset(raw_dir: Path, mapping_path: Path) -> Dataset`. Mapping YAML format:

```yaml
period_end: 2026-10-31
tables:
  invoices:
    files: ["invoices*.csv"]          # glob(s) relative to raw_dir; xlsx may add "sheet: Name"
    columns: {invoice_id: "Invoice ID", net_amount: "Net"}   # canonical: source column
    defaults: {currency: EUR}
```
  Amount-like columns (`qty, unit_price, net_amount, tax_rate, tax_amount, gross_amount, amount, debit, credit, rate_to_eur, rate`) go through `parse_amount`; `*_date`, `changed_at`, `valid_from`, `valid_to`, `service_period_end`, `date` through `parse_date`; `iban`, `counterparty_iban` through `clean_iban`. Unmapped source columns are kept with prefix `x_`. Tables in the mapping whose files match nothing are skipped (a warning, not an error).

- [ ] **Step 1: Failing tests**

```python
def test_load_maps_and_normalises(tmp_path):
    (tmp_path/"inv.csv").write_text("Invoice ID;Net;Vendor\nA1;1.000,50;V1\n", encoding="utf-8")
    m = tmp_path/"m.yaml"; m.write_text("period_end: 2026-10-31\ntables:\n  invoices:\n    files: ['inv.csv']\n    columns: {invoice_id: 'Invoice ID', net_amount: Net, vendor_id: Vendor}\n    defaults: {currency: EUR}\n")
    ds = load_dataset(tmp_path, m)
    row = ds.table("invoices").iloc[0]
    assert row.net_amount == 1000.5 and row.currency == "EUR"
    assert row.source_file == "inv.csv" and row.source_row == 2

def test_unmatched_table_skipped(tmp_path): ...  # mapping lists contracts with no file -> ds.has("contracts") is False

def test_profile_lists_columns(tmp_path): ...    # profile_dir output contains "Invoice ID" and "inv.csv"
```

  (`source_row` = 1-based line number in the file including header, so row 1 of data is 2.) CSV delimiter is sniffed (`,` `;` `\t`).
- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS. **Step 5:** Commit `feat: profiler and yaml-driven ingest`.

---

### Task 4: Duplicate checks

**Files:** Create `src/closeops/checks/duplicates.py`, `tests/checks/test_duplicates.py`

**Interfaces:**
- Consumes: `Dataset`, `Finding`, normalisers
- Produces: `check_duplicates(ds: Dataset, policy: dict) -> list[Finding]`. Checks (`check` names): `dup_exact` (same vendor_id + norm_invoice_no + gross, conf 0.99, high), `dup_near` (same norm_vendor name or vendor_id, |gross diff| ≤ 0.01, invoice dates within `policy.duplicates.date_window_days` (7), norm_invoice_no equal or rapidfuzz ratio ≥ `policy.duplicates.invoice_no_similarity` (85), conf 0.8, medium), `dup_cross_entity` (same vendor + norm invoice no in two entities, conf 0.9, high), `dup_payment` (two outgoing bank lines same counterparty_iban + amount within 3 days, conf 0.85, high). `record_ids` = [later record, earlier record] — the later one is the duplicate. Credit notes (net < 0) are excluded from invoice duplicate checks.

- [ ] **Step 1: Failing tests**

```python
def test_exact_duplicate_flags_later(make_ds, policy):
    ds = make_ds(invoices=[inv("I1","V1","INV-001","2026-10-01",121.0), inv("I2","V1","inv 1","2026-10-05",121.0)])
    f = check_duplicates(ds, policy)
    assert [(x.check, x.record_ids) for x in f] == [("dup_exact", ["I2","I1"])]

def test_same_number_different_vendor_not_duplicate(make_ds, policy):
    ds = make_ds(invoices=[inv("I1","V1","100","2026-10-01",50.0), inv("I2","V2","100","2026-10-01",50.0)])
    assert check_duplicates(ds, policy) == []

def test_credit_note_not_duplicate(make_ds, policy):
    ds = make_ds(invoices=[inv("I1","V1","7","2026-10-01",100.0), inv("I2","V1","7","2026-10-02",-100.0)])
    assert check_duplicates(ds, policy) == []

def test_near_duplicate_within_window(...): ...   # "INV-77" vs "INV-77A", 3 days apart, same gross -> dup_near
def test_duplicate_payment(...): ...               # two -500.00 lines to same IBAN 1 day apart -> dup_payment
```

  `inv(...)` and a `policy` fixture (loads `config/policy.yaml`, created here with the `duplicates` section) go in `tests/conftest.py`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: duplicate checks`.

---

### Task 5: Tax checks

**Files:** Create `src/closeops/checks/tax.py`, `tests/checks/test_tax.py`; extend `config/policy.yaml` (`tax.tolerance: 0.02`, `tax.eu_countries: [...]` 27 ISO codes)

**Interfaces:**
- Produces: `check_tax(ds, policy) -> list[Finding]`, category `tax`. Checks: `tax_math` (|net + tax − gross| > tolerance, conf 0.99), `tax_amount` (|net × rate − tax| > tolerance, conf 0.97), `tax_rate` (rate not in `tax_rates` for buyer_country, or not the standard rate where tax_rates has only one kind, conf 0.85), `tax_reverse_charge` (vendor_country ≠ buyer_country, both EU, tax_amount ≠ 0 → should be 0; or domestic with tax 0 and rate standard > 0, conf 0.8), `tax_missing_vat_id` (vendor with null vat_id and tax > 0, low, conf 0.6). `evidence` includes `expected_tax`, `booked_tax`, `difference`. Rates may arrive as 21 or 0.21 — normalise to fraction (> 1 ⇒ /100). Credit notes use absolute values.

- [ ] **Step 1: Failing tests** — one test per check above, plus:

```python
def test_credit_note_with_correct_negative_tax_is_clean(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("C1", net=-100, rate=21, tax=-21, gross=-121, vc="ES", bc="ES")],
                 tax_rates=[{"country":"ES","rate":21,"kind":"standard"}])
    assert check_tax(ds, policy) == []

def test_rate_given_as_percent_or_fraction_equivalent(...): ...  # rate 21 and 0.21 both clean
```
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: tax checks`.

---

### Task 6: Fraud / bank-change checks

**Files:** Create `src/closeops/checks/fraud.py`, `tests/checks/test_fraud.py`; extend policy (`fraud.iban_change_window_days: 30`, `fraud.approval_limit: 10000`, `fraud.split_window_days: 7`, `fraud.round_amount_min: 5000`, `fraud.export_soft_signals: false`)

**Interfaces:**
- Produces: `check_fraud(ds, policy) -> list[Finding]`, category `fraud`, `record_type` `bank_transaction` unless stated. Checks:
  - `iban_changed_before_payment` — payment to a vendor whose `vendor_bank_history.changed_at` is ≤ window days before `txn_date`; high, conf 0.9; evidence old/new IBAN, changed_by, days_between
  - `iban_not_in_master` — outgoing payment whose counterparty_iban matches no vendor's current or historic IBAN; high, conf 0.85
  - `iban_country_mismatch` — IBAN country prefix ≠ vendor country; medium, conf 0.7
  - `split_invoices` (record_type invoice) — same vendor, ≥ 2 invoices within split window, each < approval_limit, sum ≥ approval_limit; medium, conf 0.7
  - `shared_iban` — one IBAN on two different vendors; high, conf 0.85
  - soft: `round_amount`, `weekend_posting` — severity low, conf 0.4; returned only for evidence/agents; exporter drops them unless `export_soft_signals`
- Vendor ↔ bank line linking: by IBAN (current or historic), else by `norm_vendor(counterparty_name)` exact match.

- [ ] **Step 1: Failing tests** — one positive test per check plus negatives: IBAN change 45 days before payment → no `iban_changed_before_payment`; incoming payments (amount > 0) never flagged by IBAN checks.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: fraud checks`.

---

### Task 7: Purchasing checks (3-way match, contract price)

**Files:** Create `src/closeops/checks/purchasing.py`, `tests/checks/test_purchasing.py`; policy `match.price_tolerance_pct: 2`, `match.qty_tolerance: 0`

**Interfaces:**
- Produces: `check_purchasing(ds, policy) -> list[Finding]`, category `match`, record_type `invoice`. Checks: `no_po` (invoice with po_id null or unknown; medium 0.8), `po_price_mismatch` (unit_price differs from PO by > tolerance %; 0.85), `qty_over_received` (invoice qty > sum qty_received for the PO; high 0.9; skipped with no finding if `goods_receipts` absent), `contract_price_mismatch` (active contract for vendor+item on invoice_date with different unit_price beyond tolerance; 0.85; skipped if `contracts` absent). Also `match_status(ds, policy) -> pd.DataFrame` with columns invoice_id, po_id, status (`matched|price_mismatch|qty_mismatch|no_po|no_receipt`) — exported as reconciliation output.

- [ ] **Step 1: Failing tests** — matched invoice → no findings and status `matched`; 3 % price diff → `po_price_mismatch`; 1.5 % → none; dataset without goods_receipts → no `qty_over_received` and status `no_receipt`, no exception.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: purchasing match checks`.

---

### Task 8: Bank reconciliation

**Files:** Create `src/closeops/checks/bankrec.py`, `tests/checks/test_bankrec.py`; policy `bankrec.date_window_days: 5`, `bankrec.max_group: 4`, `bankrec.amount_tolerance: 0.01`, `bankrec.fx_tolerance_pct: 1`

**Interfaces:**
- Produces: `reconcile_bank(ds, policy) -> tuple[pd.DataFrame, list[Finding]]`. DataFrame columns: txn_id, matched_ids (list of invoice_id or je_id), method (`exact|window|group|fx|reference`), confidence. Matching order per entity: (1) reference contains an invoice_no (normalised) and amount equals gross → `reference` 0.99; (2) same amount, same counterparty, date within window → `exact`/`window`; (3) one bank line ↔ 2..max_group open invoices of the same vendor summing to the amount (bounded subset-sum) → `group` 0.8; (4) different currency: convert both to EUR via `fx_rates` (nearest date on or before) and match within fx_tolerance_pct → `fx` 0.75. Unmatched bank lines → Finding `bank_unmatched` (category bankrec, medium, conf 0.7). Each invoice is matched at most once.

- [ ] **Step 1: Failing tests** — reference match; window match 3 days apart; group match of 2 invoices; **EUR invoice 1000 paid by USD bank line 1085 with rate_to_eur 0.9217 → `fx` match**; unmatched line → finding; one invoice not matched twice by two equal bank lines (second becomes unmatched).
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: bank reconciliation`.

---

### Task 9: GL / close checks

**Files:** Create `src/closeops/checks/gl.py`, `tests/checks/test_gl.py`

**Interfaces:**
- Produces: `check_gl(ds, policy) -> list[Finding]`, category `gl`: `je_unbalanced` (per je_id debit ≠ credit; high 0.99; record_type gl_entry), `tb_unbalanced` (per entity; record_ids [entity]), `intercompany_mismatch` (entity A's entries with counterparty_entity B vs B's with counterparty_entity A: net amounts in EUR don't offset within 0.01; record_ids [A, B]; 0.85), `cutoff` (invoice with invoice_date > period_end but service_period_end ≤ period_end → accrual needed; or invoice_date ≤ period_end but service_period_end > period_end → deferral; record_type invoice; 0.8). Also `trial_balance(ds) -> pd.DataFrame` (entity, account, debit, credit, balance).

- [ ] **Step 1: Failing tests** — one per check, plus balanced intercompany pair → no finding.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: GL and close checks`.

---

### Task 10: Registry, policy tiers, evidence log

**Files:** Create `src/closeops/checks/registry.py`, `src/closeops/policy.py`, `src/closeops/evidence.py`, tests for each; complete `config/policy.yaml` (`materiality_eur: 5000`, `tiers.auto_min_confidence: 0.9`, `tiers.hold_below_confidence: 0.6`, `tiers.auto_categories: [duplicate, tax, match, bankrec]`)

**Interfaces:**
- Consumes: all `check_*` functions (Tasks 4–9)
- Produces:
  - `run_checks(ds, policy) -> tuple[list[Finding], dict[str, pd.DataFrame], list[str]]` — findings, side tables (`bank_matches`, `match_status`, `trial_balance`), notes (e.g. `"skipped qty_over_received: no goods_receipts"`). A check that raises is caught, logged as a note, and the run continues. Also fills `amount_eur` on each finding.
  - `load_policy(path) -> dict`; `assign_tiers(findings, policy) -> list[Finding]` with rules in this order: any check starting `iban_` or `shared_iban` → HOLD; category fraud and severity high → HOLD; `amount_eur ≥ materiality_eur` → HOLD; confidence < hold_below_confidence → HOLD; confidence ≥ auto_min_confidence and category in auto_categories → AUTO; else AUTO_NOTE. Sets `requires_human_review = tier is HOLD`.
  - `class EvidenceLog(path)`: `append(actor: str, action: str, inputs: list[str], evidence: dict, output: dict, confidence: float | None, tier: str | None) -> dict` (adds id, ts, prev_hash, hash = sha256(prev_hash + json.dumps(event_without_hash, sort_keys=True))); `verify(path) -> bool` (static).

- [ ] **Step 1: Failing tests** — tier rules table-driven (one case per rule, check ordering: high-confidence IBAN finding is still HOLD); evidence log: append 3, `verify` True, edit line 2 on disk, `verify` False; registry: a check that raises yields a note and other findings still return; dataset with only invoices runs without error.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: check registry, autonomy policy, evidence log`.

---

### Task 11: Synthetic dataset generator + answer key

**Files:** Create `src/closeops/synth/generate.py`, `config/mappings.synth.yaml`, `tests/synth/test_generate.py`

**Interfaces:**
- Consumes: `load_dataset` (Task 3)
- Produces: `generate(out_dir: Path, seed: int = 7, n_invoices: int = 600) -> Path` writing *messy* raw files: `vendors.csv` (`;` delimiter, EU decimals), `vendor_bank_changes.csv`, `purchase_orders.xlsx`, `goods_receipts.csv`, `invoices.csv` (mixed date formats, invoice no. formats), `contracts.json`, `bank_statement_<entity>.csv` ×3, `gl.csv`, `accounts.csv`, `fx.csv`, `tax_rates.csv`, and `answer_key.json`: `[{"category": ..., "check": ..., "record_id": ...}]`. 3 entities (ES, DE, FR; EUR + one USD bank account), period 2026-10. Planted traps (counts fixed by seed): 12 exact dups, 6 near dups, 2 cross-entity dups, 3 duplicate payments, 8 tax math/amount errors, 4 wrong rates, 4 reverse-charge errors, 4 IBAN-changed-then-paid, 2 payments to unknown IBAN, 2 split-invoice clusters, 1 shared IBAN, 10 PO price mismatches, 5 contract price mismatches, 5 qty over received, 6 no-PO, 8 unmatched bank lines, 2 intercompany mismatches, 6 cut-off items; plus decoys that must NOT be flagged: credit notes, same invoice no. different vendor, IBAN change 60 days before payment, 1.5 % price variance.

- [ ] **Step 1: Failing tests** — generate into tmp_path; `load_dataset(tmp_path, config/mappings.synth.yaml)` loads all 11 tables; answer_key has the counts above per check; same seed → identical files (hash compare).
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: synthetic dataset with answer key`.

---

### Task 12: Scorer, pipeline, CLI — end-to-end

**Files:** Create `src/closeops/scoring.py`, `src/closeops/pipeline.py`, `src/closeops/cli.py`, `tests/test_scoring.py`, `tests/test_e2e.py`

**Interfaces:**
- Consumes: Tasks 3, 10, 11
- Produces:
  - `score(findings: list[Finding], answer_key: list[dict]) -> dict` — per category and overall: tp, fp, fn, precision, recall, f1. A finding hits a key entry when category matches and `record_ids[0] == record_id`. Soft signals excluded.
  - `@dataclass RunResult`: dataset, findings, side_tables, notes, journal_entries (DataFrame, empty until Task 13), payments (DataFrame, empty until Task 13), run_dir: Path
  - `run_close(raw_dir: Path, mapping: Path, policy_path: Path, out_dir: Path, use_agents: bool = False) -> RunResult` — creates `out_dir/<timestamp>/`, writes `evidence.jsonl` (one event per finding + one per stage), calls exporter (Task 14 later; until then writes `findings.json`)
  - CLI: `closeops profile <raw_dir>`; `closeops synth <out_dir> [--seed]`; `closeops run <raw_dir> --mapping m.yaml [--policy] [--out results] [--agents]`; `closeops score <run_dir> <answer_key.json>` printing a table and appending a line to `scoreboard.md`

- [ ] **Step 1: Failing tests** — scorer unit tests (perfect, one fp, one fn); e2e: generate seed 7 → `run_close` → `score` → overall precision ≥ 0.9 and recall ≥ 0.85, each of duplicate/tax/fraud f1 ≥ 0.85, runtime < 60 s, `EvidenceLog.verify` True.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; when e2e thresholds miss, fix the *check logic* (never the generator's answer key) and re-run.
- [ ] **Step 4:** Run full suite → PASS. Generate seed 99 (unseen) and run CLI end-to-end; record its score in `scoreboard.md`. **Step 5:** Commit `feat: pipeline, scorer and CLI`.

---

### Task 13: Journal entries + payment proposals

**Files:** Create `src/closeops/journal.py`, `tests/test_journal.py`; policy `accounts: {ap: "4000", vat_input: "4720", expense_default: "6000", accrued: "4110", deferred: "4800", suspense: "5550"}` (placeholders, remapped on the day)

**Interfaces:**
- Consumes: `Finding`, `Dataset`, `assign_tiers`
- Produces:
  - `propose_journal_entries(ds, findings, policy) -> pd.DataFrame` columns: je_id, entity, date, account, debit, credit, currency, reason, finding_id, tier. Rules: confirmed duplicate posted invoice → reversal (Dr ap / Cr expense_default + vat_input); tax_amount error → correction of the difference between vat_input and ap; cutoff accrual → Dr expense_default / Cr accrued; deferral → Dr deferred / Cr expense_default; unmatched bank line → suspense. Every JE balances; `je_id = "ADJ-" + 4-digit sequence`; tier inherited from the finding.
  - `propose_payments(ds, findings, policy) -> pd.DataFrame` columns: invoice_id, vendor_id, entity, amount, currency, iban, due_date, tier, block_reason. Open invoices only, excluding those with duplicate or fraud findings (listed with tier HOLD and block_reason); vendor IBAN changed within window → HOLD, block_reason `"iban_changed"`.
- Wire both into `run_close`.

- [ ] **Step 1: Failing tests** — each JE type balances and has the right accounts; open invoice whose vendor changed IBAN 10 days ago → payment tier HOLD with `iban_changed`; clean open invoice → tier AUTO; duplicate invoice never appears as a payable non-HOLD row.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: journal and payment proposals`.

---

### Task 14: Exporters

**Files:** Create `src/closeops/export/generic.py`, `src/closeops/export/event.py`, `tests/export/test_generic.py`

**Interfaces:**
- Consumes: `RunResult`
- Produces: `write_results(run: RunResult, policy: dict) -> list[Path]` writing to `run.run_dir`: `findings.csv` + `findings.json` (columns: finding_id, check, category, severity, entity, record_type, primary_record_id, record_ids, confidence, amount_eur, tier, requires_human_review, explanation, resolved_by, source_refs), `journal_entries.csv`, `payment_proposals.csv`, `bank_reconciliation.csv`, `match_status.csv`, `trial_balance.csv`, `close_report.md` (per entity: findings by category, reconciled %, TB balanced, HOLD count, notes), plus a copy of the policy used. Soft signals omitted unless `fraud.export_soft_signals`. `source_refs` = `"file:row"` list resolved from the dataset.
  - `event.py`: `def export_event(run: RunResult, out_dir: Path) -> list[Path]: raise NotImplementedError("write on build day once the submission format is announced")`

- [ ] **Step 1: Failing tests** — run e2e on a small synth (n_invoices=80), assert all files exist, findings.csv row count == exported findings, every row has non-empty source_refs, close_report mentions each entity.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat: result exporters`.

---

### Task 15: Demo dashboard

**Files:** Create `dashboard/app.py`, `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `run_close`, exported files, `EvidenceLog.verify`
- Produces: Streamlit app (`streamlit run dashboard/app.py`) with sidebar: raw dir, mapping, agents toggle, **Run close** button (shows per-stage progress from `st.status`). Tabs: **Overview** (KPI tiles: findings by category, reconciled %, HOLD count, evidence chain verified ✓), **Exceptions** (filterable table by category/tier; selecting one shows evidence JSON, source rows from the dataset, agent explanation), **Journal & Payments** (tables; blocked payments highlighted), **Close report** (renders close_report.md), **Human review** (each HOLD with Approve/Reject buttons → appends a `human` event to evidence.jsonl and updates the tier shown). Loads the latest run from `results/` if no run in session.

- [ ] **Step 1: Failing test** — `streamlit.testing.v1.AppTest.from_file("dashboard/app.py")` with a pre-generated small run dir in session state: runs without exception, Overview shows the HOLD count; clicking Approve on the first HOLD adds a `human` event and `EvidenceLog.verify` stays True.
- [ ] **Step 2–4:** FAIL → implement → PASS; also launch manually and click through. **Step 5:** Commit `feat: demo dashboard`.

---

### Task 16: Agent team on residuals

**Files:** Create `src/closeops/agents/tools.py`, `src/closeops/agents/team.py`, `tests/agents/test_team.py`. Before writing, read the `claude-api` skill for current SDK usage.

**Interfaces:**
- Consumes: `Dataset`, `Finding`, `EvidenceLog`
- Produces:
  - tools (plain functions, also wrapped as agent tools): `get_records(record_type: str, ids: list[str]) -> list[dict]`, `vendor_profile(vendor_id: str) -> dict` (incl. bank history, invoice stats), `similar_invoices(invoice_id: str) -> list[dict]`, `tax_rule(country: str) -> dict`
  - `class LLM(Protocol)`: `def resolve(self, role: str, model: str, finding: dict, context: dict) -> dict` returning `{"verdict": "confirm|dismiss|escalate", "confidence": float, "explanation": str}`; real impl `ClaudeLLM` (Claude Agent SDK/Messages API with tools above, JSON output); `FakeLLM` in tests.
  - `resolve_residuals(ds, findings, policy, llm: LLM, log: EvidenceLog) -> list[Finding]` — only findings with `hold_below_confidence ≤ confidence < auto_min_confidence` or category fraud go to an agent. Role by category: duplicate/match → AP Clerk, bankrec → Reconciler, tax → Tax Reviewer, fraud → Fraud Analyst; model `claude-sonnet-5-5`. `dismiss` removes the finding (logged), `confirm` sets confidence to max(old, agent), `escalate` forces HOLD; `resolved_by` = role; explanation filled. Then **Controller** (`claude-opus-5-5`) gets the summary of all findings + JEs and returns a close memo (markdown) and `close_ready: bool`, written to `close_memo.md`. Concurrency: thread pool of 8; any agent error → finding unchanged + note (rules-only fallback). Tiers re-assigned after agents, so IBAN rules still force HOLD.
  - `run_close(..., use_agents=True)` calls this between checks and journal.

- [ ] **Step 1: Failing tests** with `FakeLLM`: high-confidence findings never sent; dismiss removes; escalate → HOLD; an agent `confirm` with 0.99 on an `iban_changed_before_payment` still ends HOLD; FakeLLM raising → finding unchanged and note recorded; each agent call produces one evidence event with actor = role.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Manual: run with real API on seed 99, compare `closeops score` with and without `--agents`, record both in `scoreboard.md`. Commit `feat: agent team on residuals`.

---

### Task 17: Rehearsal + build-day runbook

**Files:** Create `RUNBOOK.md`; modify nothing else unless the rehearsal finds bugs (fix with a test first)

- [ ] **Step 1:** Generate seed 2026 into `data/rehearsal/`, rename/reorder its columns by hand (simulate unseen format), then time: profile → write mapping → run → score. Target < 30 min to first scored output. Note every friction point.
- [ ] **Step 2:** Write `RUNBOOK.md`: the day-of timeline from spec §5, exact commands, mapping-writing checklist (which canonical columns each check needs, so missing columns are spotted early), the exporter-on-the-day checklist, submission log format for `scoreboard.md`, demo script (3 min, from spec §4.8), fallback (pre-recorded run in `results/demo/`).
- [ ] **Step 3:** Run full test suite → PASS. Commit `docs: build-day runbook and rehearsal fixes`.
