import pytest

from closeops.model import Finding, Tier
from closeops.policy import assign_tiers, load_policy


def f(check="tax_math", category="tax", severity="medium", conf=0.95, eur=100.0):
    return Finding(check=check, category=category, severity=severity, entity="E1",
                   record_type="invoice", record_ids=["I1"], evidence={}, confidence=conf,
                   suggested_action="x", amount_eur=eur)


@pytest.mark.parametrize("finding,expected", [
    (f("iban_changed_before_payment", "fraud", "high", 0.99), Tier.HOLD),
    (f("iban_country_mismatch", "fraud", "low", 0.99, 1.0), Tier.HOLD),
    (f("shared_iban", "fraud", "low", 0.99, 1.0), Tier.HOLD),
    (f("split_invoices", "fraud", "high", 0.99, 1.0), Tier.HOLD),
    (f("tax_math", "tax", "low", 0.99, 5000.0), Tier.HOLD),
    (f("tax_math", "tax", "low", 0.59, 1.0), Tier.HOLD),
    (f("tax_math", "tax", "low", 0.9, 1.0), Tier.AUTO),
    (f("round_amount", "fraud", "low", 0.95, 1.0), Tier.AUTO_NOTE),
    (f("tax_rate", "tax", "low", 0.85, 1.0), Tier.AUTO_NOTE),
    (f("tax_rate", "tax", "low", 0.6, 1.0), Tier.AUTO_NOTE),
    (f("je_unbalanced", "gl", "low", 0.99, 1.0), Tier.AUTO_NOTE),
])
def test_rules(finding, expected):
    out = assign_tiers([finding], load_policy("config/policy.yaml"))
    assert out[0].tier is expected
    assert out[0].requires_human_review is (expected is Tier.HOLD)


def test_mutates_in_place_and_returns_same_list():
    fs = [f()]
    assert assign_tiers(fs, load_policy("config/policy.yaml")) is fs
