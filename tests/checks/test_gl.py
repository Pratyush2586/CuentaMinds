from datetime import date

from closeops.checks.gl import check_gl, trial_balance


def gl(je, entity, account, debit=None, credit=None, cp=None, cur="EUR", d="2026-10-15"):
    return {"je_id": je, "entity": entity, "posting_date": date.fromisoformat(d),
            "account": account, "debit": debit, "credit": credit, "currency": cur,
            "counterparty_entity": cp}


def _ids(fs, check):
    return [f.record_ids for f in fs if f.check == check]


def test_je_unbalanced(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "100", debit=100.0), gl("J1", "E1", "200", credit=90.0),
                             gl("J2", "E1", "100", debit=50.0), gl("J2", "E1", "200", credit=50.0)])
    fs = [f for f in check_gl(ds, policy) if f.check == "je_unbalanced"]
    assert [f.record_ids for f in fs] == [["J1"]]
    f = fs[0]
    assert (f.severity, f.confidence, f.record_type, f.entity, f.category) == \
        ("high", 0.99, "gl_entry", "E1", "gl")
    assert f.amount_eur == 10.0 and f.evidence["difference"] == 10.0


def test_je_within_tolerance_ok(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "100", debit=100.0), gl("J1", "E1", "200", credit=99.99)])
    assert check_gl(ds, policy) == []


def test_tb_unbalanced_per_entity(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "100", debit=100.0), gl("J1", "E1", "200", credit=100.0),
                             gl("J2", "E2", "100", debit=70.0), gl("J2", "E2", "200", credit=60.0)])
    fs = check_gl(ds, policy)
    assert _ids(fs, "tb_unbalanced") == [["E2"]]
    f = [f for f in fs if f.check == "tb_unbalanced"][0]
    assert f.record_type == "entity" and f.severity == "high" and f.amount_eur == 10.0


def test_intercompany_mismatch(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "1300", debit=100.0, cp="E2"),
                             gl("J1", "E1", "4000", credit=100.0),
                             gl("J2", "E2", "2300", credit=80.0, cp="E1"),
                             gl("J2", "E2", "5000", debit=80.0)])
    fs = [f for f in check_gl(ds, policy) if f.check == "intercompany_mismatch"]
    assert [f.record_ids for f in fs] == [["E1", "E2"]]
    f = fs[0]
    assert f.entity == "E1" and f.amount_eur == 20.0 and f.confidence == 0.85
    assert f.evidence == {"net_a_eur": 100.0, "net_b_eur": -80.0, "difference": 20.0}


def test_intercompany_balanced_pair_no_finding(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "1300", debit=100.0, cp="E2"),
                             gl("J1", "E1", "4000", credit=100.0),
                             gl("J2", "E2", "2300", credit=100.0, cp="E1"),
                             gl("J2", "E2", "5000", debit=100.0)])
    assert check_gl(ds, policy) == []


def test_intercompany_one_side_missing_and_fx(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "1300", debit=100.0, cp="E2", cur="USD"),
                             gl("J1", "E1", "4000", credit=100.0, cur="USD")],
                 fx_rates=[{"date": date(2026, 10, 1), "currency": "USD", "rate_to_eur": 0.5},
                           {"date": date(2026, 11, 15), "currency": "USD", "rate_to_eur": 9.0}])
    fs = [f for f in check_gl(ds, policy) if f.check == "intercompany_mismatch"]
    assert len(fs) == 1 and fs[0].amount_eur == 50.0 and fs[0].evidence["net_b_eur"] == 0.0


def test_intercompany_restricted_to_intercompany_accounts(make_ds, policy):
    ds = make_ds(gl_entries=[gl("J1", "E1", "1300", debit=100.0, cp="E2"),
                             gl("J1", "E1", "4000", credit=100.0, cp="E2"),
                             gl("J2", "E2", "2300", credit=100.0, cp="E1"),
                             gl("J2", "E2", "5000", debit=100.0, cp="E1")],
                 accounts=[{"account": "1300", "name": "IC recv", "type": "Intercompany"},
                           {"account": "2300", "name": "IC pay", "type": "INTERCOMPANY payable"},
                           {"account": "4000", "name": "Rev", "type": "revenue"}])
    assert check_gl(ds, policy) == []


def test_cutoff(make_ds, policy):
    def i(iid, d, s):
        return {"invoice_id": iid, "entity": "E1", "invoice_date": date.fromisoformat(d),
                "service_period_end": date.fromisoformat(s) if s else None, "net_amount": 120.0}
    ds = make_ds(invoices=[i("A", "2026-11-05", "2026-10-20"), i("D", "2026-10-10", "2026-11-30"),
                           i("OK", "2026-10-10", "2026-10-31"), i("N", "2026-11-05", None)])
    fs = check_gl(ds, policy)
    assert [(f.record_ids, f.evidence["kind"]) for f in fs] == [(["A"], "accrual"), (["D"], "deferral")]
    f = fs[0]
    assert f.record_type == "invoice" and f.confidence == 0.8 and f.severity == "medium"
    assert f.evidence == {"kind": "accrual", "invoice_date": "2026-11-05",
                          "service_period_end": "2026-10-20", "period_end": "2026-10-31",
                          "net_amount": 120.0}


def test_missing_tables_no_findings(make_ds, policy):
    ds = make_ds()
    assert check_gl(ds, policy) == []
    tb = trial_balance(ds)
    assert list(tb.columns) == ["entity", "account", "debit", "credit", "balance"] and tb.empty


def test_trial_balance(make_ds):
    ds = make_ds(gl_entries=[gl("J1", "E2", "200", credit=10.0), gl("J1", "E1", "300", debit=5.0),
                             gl("J2", "E1", "100", debit=0.1), gl("J2", "E1", "100", debit=0.2),
                             gl("J2", "E1", "100", credit=0.4)])
    tb = trial_balance(ds)
    assert list(tb.columns) == ["entity", "account", "debit", "credit", "balance"]
    assert tb[["entity", "account"]].values.tolist() == [["E1", "100"], ["E1", "300"], ["E2", "200"]]
    assert tb["debit"].tolist() == [0.3, 5.0, 0.0]
    assert tb["balance"].tolist() == [-0.1, 5.0, -10.0]
