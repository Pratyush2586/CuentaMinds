"""bankrec command line.

    bankrec train-nonitem --data DATA                      # learn the no-item category model from TRAIN
    bankrec run --data DATA --phase sample|live|final [--out FILE]   # writes the submission, scores it if a golden exists
    bankrec score --data DATA --phase sample|train FILE
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from bankrec import data as D
from bankrec.advices import load_advices
from bankrec.engine import Engine
from bankrec.nonitem import NonItemModel
from bankrec.submission import score, write

DEFAULT_DATA = "data/valtermia"
MODEL = "nonitem.json"
LOG = "bankrec_scoreboard.md"


def _model_path(root: Path) -> Path:
    return root / "cache" / MODEL


def cmd_train_nonitem(a) -> int:
    root = Path(a.data)
    tdir = D.find_phase_dir(root, "train")
    txns = D.read(tdir / "bank_transactions.csv.gz")
    golden = D.read(tdir / "golden_allocations.csv.gz")
    exclude = set()
    if a.exclude_sample:
        exclude = set(D.read(D.find_phase_dir(root, "sample") / "bank_transactions.csv.gz").bank_txn_id)
    m = NonItemModel.train(txns, golden, exclude)
    m.save(_model_path(root))
    print(f"trained on {len(txns) - len(exclude)} lines -> {_model_path(root)} ({len(m.table)} keys)")
    return 0


def _print_score(res: dict) -> None:
    print(json.dumps({k: res[k] for k in ("ok", "score", "metrics")}, indent=1))
    if not res["ok"]:
        print(json.dumps(res["errors"][:10], indent=1))
    segs = sorted(res.get("segments", {}).items(), key=lambda x: -x[1]["n"])
    for k, v in segs[:70]:
        print(f"  {k:32s} n={v['n']:7d} acc={v['accuracy']:.3f}")


def cmd_run(a) -> int:
    root = Path(a.data)
    t0 = time.time()
    data = D.load(root, a.phase)
    mp = _model_path(root)
    model = NonItemModel.load(mp) if mp.exists() else None
    earlier = {"sample": ["train"], "train": [], "live": ["train"], "final": ["live"]}[a.phase]
    start = pd.Timestamp(data.txns.booking_date.min())
    advices = load_advices([data.phase_dir] + [D.find_phase_dir(root, p) for p in earlier], root / "cache",
                           min_received=(start - pd.DateOffset(days=35)).strftime("%Y-%m-%d"))
    t1 = time.time()
    eng = Engine(data, model, advices)
    eng.run()
    t2 = time.time()
    out = Path(a.out or f"submissions/{a.phase}_{datetime.now():%Y%m%d-%H%M%S}.csv.gz")
    write(eng.submission_rows(), out)
    print(f"{out}  load {t1 - t0:.0f}s match {t2 - t1:.0f}s  lines {len(data.txns)}  by stage {dict(Counter(eng.how.values()))}")
    golden = data.phase_dir / "golden_allocations.csv.gz"
    res = score(out, golden if golden.exists() else None, list(data.txns.bank_txn_id), data.categories)
    _print_score(res)
    if res["ok"]:
        shown = res["score"] if golden.exists() else "valid (no golden: upload to score)"
        line = f"| {datetime.now():%Y-%m-%d %H:%M} | {a.phase} | {out} | {shown} | {a.note} |\n"
        log = Path(LOG)
        if not log.exists():
            log.write_text("| time | phase | file | local score (validation only if no golden) | note |\n|---|---|---|---|---|\n",
                           encoding="utf-8")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line)
    return 0 if res["ok"] else 1


def cmd_score(a) -> int:
    root = Path(a.data)
    d = D.find_phase_dir(root, a.phase)
    txns = D.read(d / "bank_transactions.csv.gz")
    cats = list(D.read(D.find_masters(root, a.phase) / "categories.csv").category)
    golden = d / "golden_allocations.csv.gz"
    _print_score(score(Path(a.file), golden if golden.exists() else None, list(txns.bank_txn_id), cats))
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="bankrec")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("train-nonitem")
    s.add_argument("--data", default=DEFAULT_DATA)
    s.add_argument("--exclude-sample", action="store_true", help="hold out the sample month (honest sample scores)")
    s.set_defaults(fn=cmd_train_nonitem)
    s = sub.add_parser("run")
    s.add_argument("--data", default=DEFAULT_DATA)
    s.add_argument("--phase", default="sample", choices=D.PHASES)
    s.add_argument("--out")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("score")
    s.add_argument("file")
    s.add_argument("--data", default=DEFAULT_DATA)
    s.add_argument("--phase", default="sample", choices=D.PHASES)
    s.set_defaults(fn=cmd_score)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
