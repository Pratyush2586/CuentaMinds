"""Write a submission and score/validate it with the official scorer."""
from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path

from bankrec import official_score as S

EXCEPTIONS = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
              "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
HEADER = ["bank_txn_id", "item_id", "amount", "category", "exception"]


def write(rows: list[tuple], path: Path) -> Path:
    """rows: (bank_txn_id, item_id, amount|None, category, exception)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(HEADER)
        for t, item, amt, cat, exc in rows:
            w.writerow([t, item or "", "" if amt is None else f"{amt:.2f}", cat or "", exc or ""])
    return path


def _txn_file(txn_ids) -> bytes:
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        fh.write("bank_txn_id\n" + "\n".join(txn_ids) + "\n")
    return buf.getvalue()


def score(sub_path: Path, golden_path: Path | None, txn_ids: list[str], categories: list[str]) -> dict:
    """Official score. Without a golden (LIVE/FINAL) this is a pure validation: ok + errors."""
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as tmp:
        tf = os.path.join(tmp, "txns.csv.gz")
        Path(tf).write_bytes(_txn_file(txn_ids))
        gf = str(golden_path) if golden_path else os.path.join(tmp, "golden.csv")
        if not golden_path:
            Path(gf).write_text("bank_txn_id,item_id,amount,category,exception\n", encoding="utf-8")
        g = S.load_golden(gf, tf, EXCEPTIONS, categories)
        return S.score_submission(g, Path(sub_path).read_bytes(), str(sub_path))
