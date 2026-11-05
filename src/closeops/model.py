"""Canonical data model: table schemas, Dataset container, Tier, Finding."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum

import pandas as pd

CANONICAL: dict[str, list[str]] = {
    "vendors": ["vendor_id", "entity", "name", "country", "vat_id", "iban"],
    "vendor_bank_history": ["vendor_id", "iban", "changed_at", "changed_by"],
    "purchase_orders": ["po_id", "entity", "vendor_id", "po_date", "item", "qty",
                        "unit_price", "currency", "approved_by"],
    "goods_receipts": ["gr_id", "po_id", "receipt_date", "qty_received"],
    "invoices": ["invoice_id", "entity", "vendor_id", "invoice_no", "invoice_date",
                 "service_period_end", "po_id", "item", "qty", "unit_price", "currency",
                 "net_amount", "tax_rate", "tax_amount", "gross_amount", "vendor_country",
                 "buyer_country", "status"],
    "contracts": ["contract_id", "vendor_id", "item", "unit_price", "currency",
                  "valid_from", "valid_to", "payment_terms_days"],
    "bank_transactions": ["txn_id", "entity", "txn_date", "amount", "currency",
                          "counterparty_name", "counterparty_iban", "reference"],
    "gl_entries": ["je_id", "entity", "posting_date", "account", "debit", "credit",
                   "currency", "description", "counterparty_entity"],
    "accounts": ["account", "name", "type"],
    "fx_rates": ["date", "currency", "rate_to_eur"],
    "tax_rates": ["country", "rate", "kind"],
}

PROVENANCE = ["source_file", "source_row"]


def _columns(name: str) -> list[str]:
    return CANONICAL[name] + PROVENANCE


@dataclass
class Dataset:
    tables: dict[str, pd.DataFrame]
    period_end: date
    notes: list[str] = field(default_factory=list)  # ingest warnings shown as run notes

    def has(self, name: str) -> bool:
        return name in self.tables and not self.tables[name].empty

    def table(self, name: str) -> pd.DataFrame:
        if name in self.tables:
            return self.tables[name]
        return pd.DataFrame(columns=_columns(name))


class Tier(str, Enum):
    AUTO = "AUTO"
    AUTO_NOTE = "AUTO_NOTE"
    HOLD = "HOLD"


@dataclass
class Finding:
    check: str
    category: str  # duplicate|tax|fraud|match|bankrec|gl
    severity: str  # low|medium|high
    entity: str
    record_type: str
    record_ids: list[str]
    evidence: dict
    confidence: float
    suggested_action: str
    amount_eur: float = 0.0
    tier: Tier | None = None
    requires_human_review: bool = False
    explanation: str = ""
    resolved_by: str = "rule"

    @property
    def finding_id(self) -> str:
        return f"{self.check}:{'|'.join(self.record_ids)}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value if self.tier is not None else None
        d["finding_id"] = self.finding_id
        return d
