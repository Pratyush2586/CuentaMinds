from datetime import date, datetime

import pandas as pd
import pytest

from closeops.ingest.normalize import (
    clean_iban, norm_invoice_no, norm_vendor, parse_amount, parse_date,
)


@pytest.mark.parametrize("raw,exp", [("1.234,56", 1234.56), ("1,234.56", 1234.56), ("(500.00)", -500.0),
    ("€ 1 200", 1200.0), ("-75", -75.0), ("", None), (None, None), (12.5, 12.5),
    (float("nan"), None), ("abc", None), ("12,50", 12.5), ("1,234", 1234.0)])
def test_parse_amount(raw, exp): assert parse_amount(raw) == exp


@pytest.mark.parametrize("raw", ["2026-10-03", "03/10/2026", "03.10.2026", "2026/10/03"])
def test_parse_date_day_first(raw): assert parse_date(raw) == date(2026, 10, 3)


@pytest.mark.parametrize("raw,exp", [
    (datetime(2026, 10, 3, 12, 0), date(2026, 10, 3)), (date(2026, 10, 3), date(2026, 10, 3)),
    (pd.Timestamp("2026-10-03"), date(2026, 10, 3)), (None, None), ("", None),
    (float("nan"), None), (pd.NaT, None), ("not a date", None), ("32/13/2026", None)])
def test_parse_date_other(raw, exp): assert parse_date(raw) == exp


def test_clean_iban(): assert clean_iban(" es91 2100 0418 4502 0005 1332 ") == "ES9121000418450200051332"
def test_clean_iban_empty(): assert clean_iban(None) is None and clean_iban("  ") is None
def test_norm_vendor(): assert norm_vendor("ACME, S.L.") == norm_vendor("Acme SL") == "acme"
def test_norm_vendor_keeps_slack(): assert norm_vendor("Slack") == "slack"
def test_norm_invoice_no(): assert norm_invoice_no("INV-00123") == norm_invoice_no("inv 123") == "INV123"
