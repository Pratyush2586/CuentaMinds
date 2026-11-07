"""Inspect how the engine sees specific bank lines of a TRAIN month (dev debugging).

    python scripts/bankrec_probe.py 2025-03 BT-2025-0060633 BT-2025-0061161 ...
"""
import sys
from pathlib import Path

from bankrec import data as D
from bankrec.engine import Engine
from bankrec.text import bank_blob

month, ids = sys.argv[1], sys.argv[2:]
root = Path("data/valtermia")
data, gpath = D.load_train_month(root, month)
g = D.read(gpath)
eng = Engine(data, None, [])
tx = data.txns.set_index("bank_txn_id")
for t in ids:
    txn = tx.loc[t].to_dict() | {"bank_txn_id": t}
    blob = bank_blob(txn)
    gold = g[g.bank_txn_id == t]
    print(f"\n=== {t} {txn['entity']} {txn['booking_date']} {txn['amount']} {txn['currency']} | {blob[:160]}")
    print("golden:", gold[["item_id", "amount", "exception", "segment"]].values.tolist()[:6], "n", len(gold))
    for item in gold.item_id[gold.item_id != ""][:4]:
        k = eng.pos.get(item)
        if k is None:
            print("  ", item, "NOT IN ITEM BOOK")
            continue
        print("  ", item, "party", eng.party[k], "open", eng.open[k], "total", eng.total[k], "issue", eng.issue[k],
              "doc", eng.doc[k], "ccy", eng.ccy[k])
    parties = eng.rank_parties(txn, blob)
    print("ranked parties:", parties[:8])
    gp = {eng.party[eng.pos[i]] for i in gold.item_id if i in eng.pos}
    for p in gp:
        ks = [k for k in eng.by_party[p] if eng.open[k] > 0.005]
        print(f"  golden party {p}: {len(ks)} open items, open sum {sum(eng.open[k] for k in ks):.2f}")
    print("refs:", [eng.ids[k] for k in eng.ref_items(txn, blob)][:8])
