"""Per-exception precision/recall vs the golden: python scripts/bankrec_exc.py SUB [--month YYYY-MM]"""
import sys
from collections import Counter
from pathlib import Path
from bankrec import data as D
root = Path("data/valtermia")
if "--month" in sys.argv:
    g = D.read(root / "cache" / f"month_{sys.argv[sys.argv.index('--month') + 1]}" / "golden.csv.gz")
else:
    g = D.read(D.find_phase_dir(root, "sample") / "golden_allocations.csv.gz")
s = D.read(sys.argv[1])
key = lambda df: {(r.bank_txn_id, r.item_id): r.exception for r in df.itertuples() if r.exception}
G, S = key(g), key(s)
for c in sorted(set(G.values()) | set(S.values())):
    gk = {k for k, v in G.items() if v == c}; sk = {k for k, v in S.items() if v == c}
    tp = len(gk & sk); p = tp / len(sk) if sk else 0; r = tp / len(gk) if gk else 0
    f = 2 * p * r / (p + r) if p + r else 0
    print(f"{c:20s} gold={len(gk):5d} ours={len(sk):5d} tp={tp:5d} P={p:.2f} R={r:.2f} F1={f:.2f}")
