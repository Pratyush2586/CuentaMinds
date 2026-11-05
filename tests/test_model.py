from closeops.model import CANONICAL, Finding, Tier


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


def test_make_ds_fills_provenance(make_ds):
    ds = make_ds(invoices=[{"invoice_id": "I1"}, {"invoice_id": "I2"}])
    df = ds.table("invoices")
    assert ds.has("invoices")
    assert list(df["source_row"]) == [0, 1]
    assert set(df["source_file"]) == {"test"}
    assert df["entity"].isna().all()
    assert str(ds.period_end) == "2026-10-31"
