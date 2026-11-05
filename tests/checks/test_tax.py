import json

from closeops.checks.tax import check_tax
from helpers import inv_tax

ES = [{"country": "ES", "rate": 21, "kind": "standard"}]


def _checks(findings):
    return [(f.check, f.record_ids) for f in findings]


def test_clean_invoice(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=21, gross=121, vc="ES", bc="ES")],
                 tax_rates=ES)
    assert check_tax(ds, policy) == []


def test_tax_math(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=21, gross=125, vc="ES", bc="ES")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    assert _checks(f) == [("tax_math", ["I1"])]
    assert f[0].confidence == 0.99 and f[0].severity == "high" and f[0].category == "tax"
    assert f[0].record_type == "invoice" and f[0].entity == "E1"
    assert f[0].evidence["expected_tax"] == 25 and f[0].evidence["booked_tax"] == 21
    assert f[0].evidence["difference"] == 4


def test_tax_amount(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=19, gross=119, vc="ES", bc="ES")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    assert _checks(f) == [("tax_amount", ["I1"])]
    assert f[0].confidence == 0.97 and f[0].severity == "high"
    assert f[0].evidence["expected_tax"] == 21 and f[0].evidence["booked_tax"] == 19


def test_tax_within_tolerance_clean(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=99.99, rate=21, tax=21.0, gross=120.99, vc="ES", bc="ES")],
                 tax_rates=ES)
    assert check_tax(ds, policy) == []


def test_tax_rate_not_listed(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=19, tax=19, gross=119, vc="ES", bc="ES")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    assert _checks(f) == [("tax_rate", ["I1"])]
    assert f[0].confidence == 0.85 and f[0].severity == "medium"
    assert f[0].evidence["expected_tax"] == 21 and f[0].evidence["booked_tax"] == 19


def test_tax_rate_skipped_without_rates_for_country(make_ds, policy):
    row = inv_tax("I1", net=100, rate=19, tax=19, gross=119, vc="ES", bc="ES")
    assert check_tax(make_ds(invoices=[row]), policy) == []
    other = [{"country": "FR", "rate": 20, "kind": "standard"}]
    assert check_tax(make_ds(invoices=[row], tax_rates=other), policy) == []


def test_zero_rate_accepted_only_when_listed(make_ds, policy):
    row = inv_tax("I1", net=100, rate=0, tax=0, gross=100, vc="ES", bc="ES")
    rates = ES + [{"country": "ES", "rate": 0, "kind": "exempt"}]
    assert check_tax(make_ds(invoices=[row], tax_rates=rates), policy) == []


def test_zero_rate_unlisted_domestic_flagged(make_ds, policy):
    # no zero/exempt row: zero tax on a domestic invoice is a reverse-charge style miss
    row = inv_tax("I1", net=100, rate=0, tax=0, gross=100, vc="ES", bc="ES")
    f = check_tax(make_ds(invoices=[row], tax_rates=ES), policy)
    assert _checks(f) == [("tax_reverse_charge", ["I1"])]
    assert f[0].confidence == 0.8 and f[0].severity == "medium"
    assert f[0].evidence["expected_tax"] == 21 and f[0].evidence["booked_tax"] == 0


def test_reverse_charge_cross_border_with_tax(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=21, gross=121, vc="DE", bc="ES")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    assert _checks(f) == [("tax_reverse_charge", ["I1"])]
    assert f[0].evidence["expected_tax"] == 0 and f[0].evidence["booked_tax"] == 21
    assert f[0].evidence["difference"] == 21


def test_cross_border_zero_tax_is_clean(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=0, tax=0, gross=100, vc="DE", bc="ES")],
                 tax_rates=ES)
    assert check_tax(ds, policy) == []


def test_cross_border_non_eu_not_reverse_charge(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=21, gross=121, vc="US", bc="ES")],
                 tax_rates=ES)
    assert check_tax(ds, policy) == []


def test_domestic_rate_says_21_but_zero_tax_is_tax_amount(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=0, gross=100, vc="ES", bc="ES")],
                 tax_rates=ES)
    assert _checks(check_tax(ds, policy)) == [("tax_amount", ["I1"])]


def test_missing_vat_id(make_ds, policy):
    ds = make_ds(vendors=[{"vendor_id": "V1", "name": "Acme", "vat_id": None},
                          {"vendor_id": "V2", "name": "Bolt", "vat_id": "ES123"}],
                 invoices=[inv_tax("I1", net=100, rate=21, tax=21, gross=121, vc="ES", bc="ES"),
                           inv_tax("I2", net=100, rate=21, tax=21, gross=121, vc="ES", bc="ES",
                                   vendor_id="V2")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    assert _checks(f) == [("tax_missing_vat_id", ["I1"])]
    assert f[0].severity == "low" and f[0].confidence == 0.6


def test_missing_vat_id_skipped_without_vendors_or_with_zero_tax(make_ds, policy):
    row = inv_tax("I1", net=100, rate=21, tax=21, gross=121, vc="ES", bc="ES")
    assert check_tax(make_ds(invoices=[row], tax_rates=ES), policy) == []
    zero = inv_tax("I2", net=100, rate=0, tax=0, gross=100, vc="ES", bc="ES")
    rates = ES + [{"country": "ES", "rate": 0, "kind": "zero"}]
    ds = make_ds(vendors=[{"vendor_id": "V1", "name": "A", "vat_id": None}],
                 invoices=[zero], tax_rates=rates)
    assert check_tax(ds, policy) == []


def test_credit_note_with_correct_negative_tax_is_clean(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("C1", net=-100, rate=21, tax=-21, gross=-121, vc="ES", bc="ES")],
                 tax_rates=ES)
    assert check_tax(ds, policy) == []


def test_credit_note_wrong_tax_flagged(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("C1", net=-100, rate=21, tax=-19, gross=-119, vc="ES", bc="ES")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    assert _checks(f) == [("tax_amount", ["C1"])]
    assert f[0].evidence["booked_tax"] == 19


def test_rate_given_as_percent_or_fraction_equivalent(make_ds, policy):
    for rate in (21, 0.21):
        ds = make_ds(invoices=[inv_tax("I1", net=100, rate=rate, tax=21, gross=121, vc="ES", bc="ES")],
                     tax_rates=ES)
        assert check_tax(ds, policy) == []
    frac = [{"country": "ES", "rate": 0.21, "kind": "standard"}]
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=21, tax=21, gross=121, vc="ES", bc="ES")],
                 tax_rates=frac)
    assert check_tax(ds, policy) == []


def test_precedence_one_finding_per_invoice(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=100, rate=19, tax=15, gross=130, vc="DE", bc="ES")],
                 tax_rates=ES)
    assert _checks(check_tax(ds, policy)) == [("tax_math", ["I1"])]


def test_missing_values_skip_without_error(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I1", net=None, rate=21, tax=21, gross=121, vc="ES", bc="ES"),
                           inv_tax("I2", net=100, rate=None, tax=None, gross=None, vc="ES", bc="ES"),
                           inv_tax("I3", net=100, rate=None, tax=21, gross=121, vc=None, bc=None)],
                 tax_rates=ES)
    assert check_tax(ds, policy) == []


def test_evidence_json_plain_and_sorted(make_ds, policy):
    ds = make_ds(invoices=[inv_tax("I2", net=100, rate=21, tax=21, gross=125, vc="ES", bc="ES"),
                           inv_tax("I1", net=100, rate=21, tax=19, gross=119, vc="ES", bc="ES")],
                 tax_rates=ES)
    f = check_tax(ds, policy)
    json.dumps([x.to_dict() for x in f])
    assert [x.finding_id for x in f] == sorted(x.finding_id for x in f)
    assert len(f) == 2


def test_no_tables_returns_empty(make_ds, policy):
    assert check_tax(make_ds(), policy) == []


def test_exempt_row_without_numeric_rate_accepts_zero(make_ds, policy):
    rates = ES + [{"country": "ES", "rate": None, "kind": "exempt"}]
    row = inv_tax("I1", net=100, rate=0, tax=0, gross=100, vc="ES", bc="ES")
    assert check_tax(make_ds(invoices=[row], tax_rates=rates), policy) == []


def test_non_eu_vendor_zero_tax_not_flagged(make_ds, policy):
    row = inv_tax("I1", net=100, rate=0, tax=0, gross=100, vc="US", bc="ES")
    assert check_tax(make_ds(invoices=[row], tax_rates=ES), policy) == []
