"""Category model for bank lines that settle no item, learned from the TRAIN golden.

Key = (sign, bank_code, bank_subcode, text shape); backs off to coarser keys. Each key stores how often lines
with it were a no-item category vs settled items, so the matcher can tell "this is a fee" from "this is a customer
receipt we failed to match".
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from bankrec.text import fold

ITEM = "__ITEM__"


def shape(text: str, n_words: int = 3) -> str:
    words = re.findall(r"[A-Z]{2,}", fold(text))
    return " ".join(words[:n_words])


def keys(row) -> list[str]:
    sign = "+" if float(row["amount"] or 0) >= 0 else "-"
    code = f"{row.get('bank_code', '')}/{row.get('bank_subcode', '')}"
    sh3, sh2 = shape(row.get("text", "")), shape(row.get("text", ""), 2)
    return [f"{sign}|{code}|{sh3}", f"{sign}|{sh3}", f"{sign}|{code}|{sh2}", f"{sign}|{sh2}", f"{sign}|{code}"]


class NonItemModel:
    def __init__(self, table: dict[str, dict[str, int]]):
        self.table = table

    @classmethod
    def train(cls, txns: pd.DataFrame, golden: pd.DataFrame, exclude_txns: set[str] | None = None) -> "NonItemModel":
        noitem = golden[golden.item_id == ""].set_index("bank_txn_id").category
        table: dict[str, Counter] = defaultdict(Counter)
        for row in txns.to_dict("records"):
            t = row["bank_txn_id"]
            if exclude_txns and t in exclude_txns:
                continue
            label = noitem.get(t, ITEM)
            for k in keys(row):
                table[k][label] += 1
        return cls({k: dict(v) for k, v in table.items()})

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.table), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "NonItemModel":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def predict(self, row, min_count: int = 3) -> tuple[str, float, int]:
        """(best label, its share, support) at the most specific key with enough support; label may be ITEM."""
        for k in keys(row):
            c = self.table.get(k)
            if c:
                n = sum(c.values())
                if n >= min_count:
                    label, cnt = max(c.items(), key=lambda kv: kv[1])
                    return label, cnt / n, n
        return ITEM, 0.0, 0

    def best_category(self, row) -> str | None:
        """Most likely no-item category for this line, ignoring the ITEM label."""
        for k in keys(row):
            c = self.table.get(k)
            if c:
                cats = {a: b for a, b in c.items() if a != ITEM}
                if cats:
                    return max(cats.items(), key=lambda kv: kv[1])[0]
        return None
