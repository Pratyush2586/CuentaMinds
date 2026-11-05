from closeops.checks import registry
from closeops.checks.registry import run_checks
from helpers import inv


def test_invoices_only_runs(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "N1", "2026-10-01", 100.0)])
    fs, tables, notes = run_checks(ds, policy)
    assert set(tables) == {"bank_matches", "match_status", "trial_balance"}
    assert "skipped qty_over_received: no goods_receipts" in notes
    assert "skipped je_unbalanced: no gl_entries" in notes
    assert "skipped qty_over_received: no invoices" not in notes
    assert [x.finding_id for x in fs] == sorted(x.finding_id for x in fs)


def test_raising_check_noted(make_ds, policy, monkeypatch):
    def boom(ds, policy):
        raise ValueError("bad")
    monkeypatch.setattr(registry, "check_tax", boom)
    ds = make_ds(invoices=[inv("I1", "V1", "N1", "2026-10-01", 100.0),
                           inv("I2", "V1", "N1", "2026-10-01", 100.0)])
    fs, _, notes = run_checks(ds, policy)
    assert "error in boom: ValueError: bad" in notes or any("ValueError: bad" in n for n in notes)
    assert any(x.check.startswith("dup_") for x in fs)


def test_amount_eur_filled_from_primary(make_ds, policy):
    ds = make_ds(invoices=[inv("I1", "V1", "N1", "2026-10-01", 100.0, currency="USD"),
                           inv("I2", "V1", "N1", "2026-10-01", 100.0, currency="USD")],
                 fx_rates=[{"date": __import__("datetime").date(2026, 10, 1), "currency": "USD",
                            "rate_to_eur": 0.5}])
    fs, _, _ = run_checks(ds, policy)
    d = [x for x in fs if x.check.startswith("dup_")]
    assert d and all(x.amount_eur > 0 for x in d)


def test_failing_amount_fill_keeps_findings(make_ds, policy, monkeypatch):
    def boom(f, ds, fx):
        raise TypeError("odd cell")
    monkeypatch.setattr(registry, "_fill_amount", boom)
    ds = make_ds(invoices=[inv("I1", "V1", "N1", "2026-10-01", 100.0),
                           inv("I2", "V1", "N1", "2026-10-01", 100.0)])
    fs, _, notes = run_checks(ds, policy)
    assert any(x.check.startswith("dup_") for x in fs)
    assert [n for n in notes if n.startswith("error in _fill_amount")] == [
        f"error in _fill_amount: {len(fs)} findings without amount_eur (TypeError: odd cell)"]
