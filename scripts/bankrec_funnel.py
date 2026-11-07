"""Failure funnel (TRAIN only): why is each golden (bank line, item) pair lost?

    python scripts/bankrec_funnel.py RUN.csv.gz --month 2025-03 [--segments ar_ref_noise,ar_n_to_m]

Mechanisms per missed golden pair (t, i):
  M5 amount     pair predicted, allocated amount wrong
  M3 consumed   item i allocated to another bank line t' (an earlier/other prediction took it)
  M4 other-set  line t matched, but to other items (search chose another solution)
  M1/M2 unmatched  line t left unmatched; split by whether the item's document number (or its digit core)
                   appears in the line text (M1 = reference present but not resolved, M2 = no usable reference)
Plus exception errors split by whether the predicted item set of the line was exactly right.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from bankrec import data as D

sub = sys.argv[1]
month = sys.argv[sys.argv.index("--month") + 1]
segs = sys.argv[sys.argv.index("--segments") + 1].split(",") if "--segments" in sys.argv else None
data, gp = D.load_train_month(Path("data/valtermia"), month)
g, s = D.read(gp), D.read(sub)
txt = {r.bank_txn_id: " ".join(str(x or "") for x in (r.text, r.remittance_info, r.ref1, r.ref2, r.end_to_end_id)).upper()
       for r in data.txns.itertuples()}
doc = dict(zip(data.items.item_id, data.items.doc.astype(str).str.upper()))
seg = dict(zip(g.bank_txn_id, g.segment))
gi, si = g[g.item_id != ""], s[s.item_id != ""]
G = {(a, b): float(c) for a, b, c in zip(gi.bank_txn_id, gi.item_id, gi.amount)}
S = {(a, b): float(c) for a, b, c in zip(si.bank_txn_id, si.item_id, si.amount)}
s_items_of = si.groupby("bank_txn_id").item_id.apply(set).to_dict()
g_items_of = gi.groupby("bank_txn_id").item_id.apply(set).to_dict()
s_txn_of_item = si.groupby("item_id").bank_txn_id.apply(set).to_dict()


def ref_in_text(t: str, i: str) -> bool:
    d = doc.get(i, "")
    if not d:
        return False
    if d in txt.get(t, ""):
        return True
    core = re.sub(r"\D", "", d).lstrip("0")
    return len(core) >= 5 and core in re.sub(r"\D", " ", txt.get(t, ""))


rows = Counter()
for (t, i), amt in G.items():
    sg = seg.get(t, "?")
    if segs and sg not in segs:
        continue
    if (t, i) in S:
        if abs(S[(t, i)] - amt) <= max(0.01, 0.005 * abs(amt)):
            continue
        m = "M5 amount wrong"
    elif s_txn_of_item.get(i, set()) - {t}:
        m = "M3 consumed by other line"
    elif s_items_of.get(t):
        m = "M4 other set chosen" + (" (ref in text)" if ref_in_text(t, i) else "")
    else:
        m = "M1 unmatched, ref in text" if ref_in_text(t, i) else "M2 unmatched, no ref"
    rows[(sg, m)] += 1
extra = Counter(seg.get(t, "?") for (t, i) in S if (t, i) not in G and (not segs or seg.get(t) in segs))
print(f"== {month}  lost golden pairs by segment x mechanism")
for (sg, m), n in sorted(rows.items(), key=lambda x: -x[1])[:30]:
    print(f"  {n:6d}  {sg:28s} {m}")
print("  mechanism totals:", dict(Counter({m: 0 for _, m in rows}) + Counter({m: n for (_, m), n in rows.items()})))
tot = Counter()
for (sg, m), n in rows.items():
    tot[m] += n
print("  totals:", dict(tot.most_common()))
print("  false (extra) pairs by segment:", dict(extra.most_common(8)))
# exception errors split by item-set correctness
ge = g.groupby("bank_txn_id").exception.first().to_dict()
se = s.groupby("bank_txn_id").exception.first().to_dict()
ex = Counter()
for t, e in ge.items():
    if segs and seg.get(t) not in segs:
        continue
    if se.get(t, "") != e:
        ok = s_items_of.get(t, set()) == g_items_of.get(t, set())
        ex[("set right" if ok else "set wrong", e or "-", se.get(t, "") or "-")] += 1
print("  exception errors (set?, golden, predicted):")
for k, n in ex.most_common(12):
    print(f"  {n:6d}  {k}")
