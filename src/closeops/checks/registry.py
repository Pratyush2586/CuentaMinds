"""Runs every rule check, fills amount_eur, and collects side tables and notes."""
from __future__ import annotations

import logging

import pandas as pd

from closeops.checks._util import FxRates, _plain
from closeops.checks.bankrec import reconcile_bank
from closeops.checks.duplicates import check_duplicates
from closeops.checks.fraud import check_fraud
from closeops.checks.gl import check_gl, trial_balance
from closeops.checks.purchasing import check_purchasing, match_status
from closeops.checks.tax import check_tax
from closeops.model import Dataset, Finding

log = logging.getLogger("closeops.checks")

# check name -> tables it needs; notes are emitted only for tables that are absent.
REQUIRED: dict[str, list[str]] = {
    "dup_exact": ["invoices"], "dup_near": ["invoices"], "dup_cross_entity": ["invoices"],
    "dup_payment": ["bank_transactions"],
    "tax_math": ["invoices"], "tax_amount": ["invoices"], "tax_reverse_charge": ["invoices"],
    "tax_rate": ["invoices", "tax_rates"], "tax_missing_vat_id": ["invoices", "vendors"],
    "iban_changed_before_payment": ["bank_transactions", "vendor_bank_history"],
    "iban_not_in_master": ["bank_transactions", "vendors"],
    "iban_country_mismatch": ["bank_transactions", "vendors"],
    "shared_iban": ["vendors"],
    "split_invoices": ["invoices"], "round_amount": ["bank_transactions"],
    "weekend_posting": ["gl_entries"],
    "qty_over_received": ["goods_receipts"], "po_price_mismatch": ["purchase_orders"],
    "contract_price_mismatch": ["contracts"], "no_po": ["purchase_orders"],
    "bank_reconciliation": ["bank_transactions"],
    "je_unbalanced": ["gl_entries"], "tb_unbalanced": ["gl_entries"],
    "intercompany_mismatch": ["gl_entries"], "cutoff": ["invoices"],
}

# record_type -> (table, id column, amount column, currency column)
_PRIMARY_AMOUNT = {"invoice": ("invoices", "invoice_id", "gross_amount", "currency"),
                   "bank_transaction": ("bank_transactions", "txn_id", "amount", "currency")}
_SIDE_TABLES = ("bank_matches", "match_status", "trial_balance")


def _skip_notes(ds: Dataset) -> list[str]:
    return [f"skipped {check}: no {t}" for check, tables in REQUIRED.items()
            for t in tables if not ds.has(t)]


def _fill_amount(f: Finding, ds: Dataset, fx: FxRates) -> None:
    if f.amount_eur or f.record_type not in _PRIMARY_AMOUNT or not f.record_ids:
        return
    table, id_col, amt_col, cur_col = _PRIMARY_AMOUNT[f.record_type]
    if not ds.has(table):
        return
    df = ds.table(table)
    rows = df[df[id_col].astype(str) == str(f.record_ids[0])]
    if rows.empty:
        return
    r = rows.iloc[0]
    amount, cur = _plain(r[amt_col]), _plain(r[cur_col])
    cur = str(cur).strip().upper() if cur is not None and str(cur).strip() else "EUR"
    rate = fx.rate(cur, ds.period_end)
    if amount is not None and rate is not None:
        f.amount_eur = round(abs(float(amount)) * rate, 2)


def run_checks(ds: Dataset, policy: dict) -> tuple[list[Finding], dict[str, pd.DataFrame], list[str]]:
    notes = _skip_notes(ds)
    findings: list[Finding] = []
    tables: dict[str, pd.DataFrame] = {}

    def guarded(fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # a failing check must not stop the close
            log.exception("check function %s failed", fn.__name__)
            notes.append(f"error in {fn.__name__}: {type(exc).__name__}: {exc}")
            return None

    for fn in (check_duplicates, check_tax, check_fraud, check_purchasing, check_gl):
        findings += guarded(fn, ds, policy) or []
    res = guarded(reconcile_bank, ds, policy)
    if res is not None:
        tables["bank_matches"] = res[0]
        findings += res[1]
    for key, fn, args in (("match_status", match_status, (ds, policy)),
                          ("trial_balance", trial_balance, (ds,))):
        out = guarded(fn, *args)
        tables[key] = out if out is not None else pd.DataFrame()
    tables.setdefault("bank_matches", pd.DataFrame())

    fx = FxRates(ds)
    for f in findings:
        _fill_amount(f, ds, fx)
    findings.sort(key=lambda f: f.finding_id)
    return findings, {k: tables[k] for k in _SIDE_TABLES}, notes
