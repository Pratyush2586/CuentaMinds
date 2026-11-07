"""FINAL keeps the previous masters' names and IBANs (renames / bank changes during the phase)."""
from pathlib import Path

import pandas as pd

from bankrec import data as D

COLS = "customer_id,entity,name,trade_name,country,iban"


def _write(d: Path, rows: list[str], fx: list[str]) -> None:
    d.mkdir(parents=True)
    (d / "customers.csv").write_text(COLS + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    (d / "categories.csv").write_text("category\nBANK_FEES\n", encoding="utf-8")
    (d / "fx_rates.csv").write_text("date,currency,units_per_eur,source\n" + "\n".join(fx) + "\n", encoding="utf-8")


def test_final_keeps_old_names_and_ibans(tmp_path):
    old, new = tmp_path / "docs" / "masters", tmp_path / "final" / "masters_final"
    _write(old, ["C1,ES01,Aguas Viejas SL,,ES,ES11OLD"], ["2025-12-31,USD,1.10,x"])
    _write(new, ["C1,ES01,Aguas Nuevas SL,,PT,ES22NEW"], ["2026-01-02,USD,1.12,x"])
    data = D.Data(phase="final", phase_dir=tmp_path / "final", masters_dir=new, txns=pd.DataFrame(),
                  items=pd.DataFrame(), customers=D.read(new / "customers.csv"), suppliers=pd.DataFrame(),
                  categories=["BANK_FEES"], payment_terms=pd.DataFrame(),
                  fx=D._num(D.read(new / "fx_rates.csv"), ["units_per_eur"]),
                  bank_accounts=pd.DataFrame(), entities=pd.DataFrame())
    D._merge_previous_masters(data, tmp_path)
    c = data.customers
    assert set(c.name) == {"Aguas Viejas SL", "Aguas Nuevas SL"}
    assert set(c.iban) == {"ES11OLD", "ES22NEW"}
    assert c.iloc[-1].country == "PT"                      # final values come last (single-valued attributes)
    assert list(data.fx.date) == ["2025-12-31", "2026-01-02"]
