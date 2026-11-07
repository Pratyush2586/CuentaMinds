"""Where are the (bank line, item) pairs lost? Missed and extra pairs per golden segment, biggest first.

    python scripts/bankrec_pairs.py SUB [--month 2025-03]
"""
import sys
from pathlib import Path

from bankrec import data as D

sub = sys.argv[1]
month = sys.argv[sys.argv.index("--month") + 1] if "--month" in sys.argv else ""
root = Path("data/valtermia")
gp = root / "cache" / f"month_{month}" / "golden.csv.gz" if month else \
    D.find_phase_dir(root, "sample") / "golden_allocations.csv.gz"
g, s = D.read(gp), D.read(sub)
seg = dict(zip(g.bank_txn_id, g.segment))
G = set(zip(g.bank_txn_id[g.item_id != ""], g.item_id[g.item_id != ""]))
S = set(zip(s.bank_txn_id[s.item_id != ""], s.item_id[s.item_id != ""]))
miss, extra, tot = {}, {}, {}
for t, _ in G:
    tot[seg[t]] = tot.get(seg[t], 0) + 1
for t, _ in G - S:
    miss[seg[t]] = miss.get(seg[t], 0) + 1
for t, _ in S - G:
    k = seg.get(t, "?")
    extra[k] = extra.get(k, 0) + 1
print(f"golden pairs {len(G)}  ours {len(S)}  missed {len(G - S)}  extra {len(S - G)}  "
      f"P={len(G & S) / max(1, len(S)):.3f} R={len(G & S) / max(1, len(G)):.3f}")
keys = sorted(set(miss) | set(extra), key=lambda k: -(miss.get(k, 0) + extra.get(k, 0)))
for k in keys[:30]:
    print(f"  {k:30s} golden {tot.get(k, 0):6d}  missed {miss.get(k, 0):6d}  extra {extra.get(k, 0):6d}")
