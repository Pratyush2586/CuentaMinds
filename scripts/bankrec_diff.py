"""Compare a submission with the golden for one segment.

    python scripts/bankrec_diff.py SUB SEGMENT [N]                    # sample month
    python scripts/bankrec_diff.py SUB SEGMENT [N] --month 2025-03    # TRAIN month evaluated with run --month
"""
import sys
from pathlib import Path

from bankrec import data as D

args = [a for a in sys.argv[1:] if not a.startswith("--")]
month = sys.argv[sys.argv.index("--month") + 1] if "--month" in sys.argv else ""
if month:
    args = [a for a in args if a != month]
sub, seg = args[0], args[1]
n = int(args[2]) if len(args) > 2 else 3
root = Path("data/valtermia")
if month:
    g = D.read(root / "cache" / f"month_{month}" / "golden.csv.gz")
    t = D.read(D.find_phase_dir(root, "train") / "bank_transactions.csv.gz")
    t = t[t.bank_txn_id.isin(set(g.bank_txn_id))].set_index("bank_txn_id")
else:
    d = D.find_phase_dir(root, "sample")
    g = D.read(d / "golden_allocations.csv.gz")
    t = D.read(d / "bank_transactions.csv.gz").set_index("bank_txn_id")
s = D.read(sub)
s = s[s.bank_txn_id.isin(set(g.bank_txn_id[g.segment == seg]))]
sg = dict(tuple(s.groupby("bank_txn_id")))
bad = []
for b, gg in g[g.segment == seg].groupby("bank_txn_id"):
    ss = sg.get(b, s.iloc[0:0])
    G = {(r.item_id, round(float(r.amount or 0), 2), r.category, r.exception) for r in gg.itertuples()}
    S = {(r.item_id, round(float(r.amount or 0), 2), r.category, r.exception) for r in ss.itertuples()}
    if G != S:
        bad.append((b, G, S))
print(f"{seg}: {len(bad)} wrong of {g[g.segment == seg].bank_txn_id.nunique()}")
for b, G, S in bad[:n]:
    r = t.loc[b]
    print(f"\n{b} {r.entity} {r.statement_format} {r.booking_date} {r.amount} {r.currency} | {r.text} | cp={r.counterparty_name} "
          f"acct={r.counterparty_account} ri={r.remittance_info} r1={r.ref1} r2={r.ref2} e2e={r.end_to_end_id} orig={r.orig_amount}{r.orig_currency}")
    print("  golden only:", sorted(G - S)[:8], "" if len(G - S) <= 8 else f"... +{len(G - S) - 8}")
    print("  ours only:  ", sorted(S - G)[:8], "" if len(S - G) <= 8 else f"... +{len(S - G) - 8}")
