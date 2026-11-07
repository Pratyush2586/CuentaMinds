"""Output-only refinement: drop the UNIDENTIFIED exception on large unexplained receipts.

    python scripts/unid_cap.py SUB OUT --phase final|live [--eur 10000]
    python scripts/unid_cap.py SUB OUT --month 2025-03 [--eur 10000]      (TRAIN: also scores before/after)

TRAIN (2025-03, 2024-10): truly unidentified receipts are small (median ~550 EUR, 90 % < ~3,600); lines flagged
UNIDENTIFIED above ~10,000 EUR are almost always unreferenced multi-invoice payments of a known customer (106 of
~110 per month wrong). Above the cap the line keeps category UNIDENTIFIED (cash not applied) but no exception.
Amounts are converted to EUR with the masters' ECB rates (latest rate on or before the booking date).
"""
import sys
from pathlib import Path

import pandas as pd

from bankrec import data as D, submission as SUB

sub, out = sys.argv[1], Path(sys.argv[2])
cap = float(sys.argv[sys.argv.index("--eur") + 1]) if "--eur" in sys.argv else 10000.0
root = Path("data/valtermia")
if "--month" in sys.argv:
    data, gp = D.load_train_month(root, sys.argv[sys.argv.index("--month") + 1])
else:
    data, gp = D.load(root, sys.argv[sys.argv.index("--phase") + 1]), None
tx = data.txns[["bank_txn_id", "amount", "currency", "booking_date"]].copy()
tx["amount"] = pd.to_numeric(tx.amount)
fx = data.fx.assign(date=pd.to_datetime(data.fx.date), units_per_eur=pd.to_numeric(data.fx.units_per_eur)).sort_values("date")
tx = tx.assign(booking_date=pd.to_datetime(tx.booking_date)).sort_values("booking_date")
tx = pd.merge_asof(tx, fx.rename(columns={"date": "booking_date"}), on="booking_date", by="currency", direction="backward")
tx = tx.drop(columns=["source"], errors="ignore")
tx["eur"] = tx.amount / tx.units_per_eur.where(tx.currency != "EUR", 1.0).fillna(1.0)
big = set(tx.bank_txn_id[(tx.amount > 0) & (tx.eur > cap)])
s = D.read(sub)
noitem = set(s.bank_txn_id) - set(s.bank_txn_id[s.item_id != ""])
hit = (s.exception == "UNIDENTIFIED") & (s.item_id == "") & s.bank_txn_id.isin(big & noitem)
s.loc[hit, "exception"] = ""
rows = [(a, b or None, None if c == "" else float(c), d, e) for a, b, c, d, e in
        s[["bank_txn_id", "item_id", "amount", "category", "exception"]].itertuples(index=False)]
SUB.write(rows, out)
ids = list(data.txns.bank_txn_id)
b = SUB.score(out, gp, ids, data.categories)
print(f"lines changed {int(hit.sum())}  cap {cap:.0f} EUR  valid {b['ok']}")
if gp:
    a = SUB.score(Path(sub), gp, ids, data.categories)
    print(f"score {a['score']} -> {b['score']}  exc-F1 {a['metrics']['exception_macro_f1']:.4f} -> {b['metrics']['exception_macro_f1']:.4f}")
