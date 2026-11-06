"""Missing golden pairs and wrong lines by segment: python scripts/bankrec_missing.py SUB"""
import sys
from pathlib import Path
import pandas as pd
from bankrec import data as D
d = D.find_phase_dir(Path("data/valtermia"), "sample")
g = D.read(d / "golden_allocations.csv.gz"); s = D.read(sys.argv[1])
sp = {(a, b): float(c) for a, b, c in zip(s.bank_txn_id, s.item_id, s.amount) if b}
gi = g[g.item_id != ""]
hit = [(a, b) in sp and abs(sp[(a, b)] - float(c)) <= max(0.01, 0.005 * float(c)) for a, b, c in zip(gi.bank_txn_id, gi.item_id, gi.amount)]
gi = gi.assign(hit=hit)
t = gi.groupby("segment").agg(pairs=("hit", "size"), missed=("hit", lambda h: int((~h).sum())))
t["share_missed_%"] = (t.missed / len(gi) * 100).round(2)
print(t.sort_values("missed", ascending=False).head(18).to_string())
print("submitted pairs", len(sp), "false pairs", len(sp) - sum(hit))
