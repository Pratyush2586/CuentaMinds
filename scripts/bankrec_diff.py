"""Compare a submission with the golden for one segment: python scripts/bankrec_diff.py SUB SEGMENT [N] [--phase sample]"""
import sys
import pandas as pd
from bankrec import data as D
sub, seg = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 3
d = D.find_phase_dir(__import__("pathlib").Path("data/valtermia"), "sample")
g = D.read(d / "golden_allocations.csv.gz")
s = D.read(sub)
t = D.read(d / "bank_transactions.csv.gz").set_index("bank_txn_id")
bad = []
for b, gg in g[g.segment == seg].groupby("bank_txn_id"):
    ss = s[s.bank_txn_id == b]
    G = {(r.item_id, round(float(r.amount or 0), 2), r.category, r.exception) for r in gg.itertuples()}
    S = {(r.item_id, round(float(r.amount or 0), 2), r.category, r.exception) for r in ss.itertuples()}
    if G != S:
        bad.append((b, G, S))
print(f"{seg}: {len(bad)} wrong of {g[g.segment == seg].bank_txn_id.nunique()}")
for b, G, S in bad[:n]:
    r = t.loc[b]
    print(f"\n{b} {r.statement_format} {r.booking_date} {r.amount} {r.currency} | {r.text} | cp={r.counterparty_name} ri={r.remittance_info} r2={r.ref2} orig={r.orig_amount}{r.orig_currency}")
    print("  golden only:", sorted(G - S)[:8], "" if len(G - S) <= 8 else f"... +{len(G - S) - 8}")
    print("  ours only:  ", sorted(S - G)[:8], "" if len(S - G) <= 8 else f"... +{len(S - G) - 8}")
