"""bankrec command line.

    bankrec train-nonitem --data DATA                      # learn the no-item category model from TRAIN
    bankrec run --data DATA --phase sample|live|final [--out FILE]   # writes the submission, scores it if a golden exists
    bankrec score --data DATA --phase sample|train FILE
"""
from __future__ import annotations

import argparse
import json
import os
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
from bankrec.pdfdocs import load_pdf_facts
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
    out = _model_path(root)
    if a.exclude_month:
        exclude |= set(txns.bank_txn_id[txns.booking_date.str[:7] == a.exclude_month])
        out = root / "cache" / f"nonitem_ex_{a.exclude_month}.json"
    m = NonItemModel.train(txns, golden, exclude)
    m.save(out)
    print(f"trained on {len(txns) - len(exclude)} lines -> {out} ({len(m.table)} keys)")
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
    print(f"[{datetime.now():%H:%M:%S}] loading data ({a.phase if not a.month else a.month}): bank lines, open items, batches, advices, PDF facts", flush=True)
    mp = _model_path(root)
    if a.month:  # dev evaluation on one TRAIN month with the full open book (LIVE-like)
        a.phase = "train"
        data, golden = D.load_train_month(root, a.month)
        start = pd.Timestamp(f"{a.month}-01")
        advices = load_advices([data.phase_dir], root / "cache",
                               min_received=(start - pd.DateOffset(days=35)).strftime("%Y-%m-%d"),
                               max_received=(pd.Timestamp(data.txns.booking_date.max()) + pd.Timedelta(days=20)).strftime("%Y-%m-%d"))  # advices arrive after the payment; LIVE/FINAL ship the whole phase
        mp = root / "cache" / f"nonitem_ex_{a.month}.json" if (root / "cache" / f"nonitem_ex_{a.month}.json").exists() else mp
    else:
        data = D.load(root, a.phase)
        golden = data.phase_dir / "golden_allocations.csv.gz"
        earlier = {"sample": ["train"], "train": [], "live": ["train"], "final": ["live"]}[a.phase]
        start = pd.Timestamp(data.txns.booking_date.min())
        advices = load_advices([data.phase_dir] + [D.find_phase_dir(root, p) for p in earlier], root / "cache",
                               min_received=(start - pd.DateOffset(days=35)).strftime("%Y-%m-%d"))
    model = NonItemModel.load(mp) if mp.exists() else None
    # facts only the PDFs state (instalments, retentions, advances, cancel-and-replace, paid-out credit notes)
    try:   # a PDF problem must never stop a run: fall back to no PDF facts
        pdf_dirs = [data.phase_dir]
        if not a.month:
            for p in {"live": ["train"], "final": ["live", "train"]}.get(a.phase, []):
                try:
                    pdf_dirs.append(D.find_phase_dir(root, p))
                except FileNotFoundError:
                    pass
        data.pdf_facts = load_pdf_facts(pdf_dirs, root / "cache")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: PDF facts unavailable ({type(exc).__name__}: {exc}); continuing without them")
        data.pdf_facts = {}
    t1 = time.time()
    print(f"[{datetime.now():%H:%M:%S}] data loaded in {t1 - t0:.0f}s: {len(data.txns)} bank lines, {len(data.items)} items, "
          f"{len(data.pdf_facts)} PDF facts; building the engine indexes", flush=True)
    eng = Engine(data, model, advices)
    eng.run()
    t2 = time.time()
    out = Path(a.out or f"submissions/{a.phase}_{datetime.now():%Y%m%d-%H%M%S}.csv.gz")
    print(f"[{datetime.now():%H:%M:%S}] writing {out} and validating with the official scorer", flush=True)
    write(eng.submission_rows(), out)
    _write_stats(eng, data, out)
    print(f"{out}  load {t1 - t0:.0f}s match {t2 - t1:.0f}s  lines {len(data.txns)}  by stage {dict(Counter(eng.how.values()))}")
    res = score(out, golden if golden.exists() else None, list(data.txns.bank_txn_id), data.categories)
    _print_score(res)
    if res["ok"] and not a.no_log:
        shown = res["score"] if golden.exists() else "valid (no golden: upload to score)"
        line = f"| {datetime.now():%Y-%m-%d %H:%M} | {a.phase}{' ' + a.month if a.month else ''} | {out} | {shown} | {a.note} |\n"
        log = Path(LOG)
        if not log.exists():
            log.write_text("| time | phase | file | local score (validation only if no golden) | note |\n|---|---|---|---|---|\n",
                           encoding="utf-8")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line)
    return 0 if res["ok"] else 1


def _write_stats(eng: Engine, data, out: Path) -> None:
    """Label-free run internals next to the submission (<out>.stats.json), used by scripts/final_gate.py."""
    ent = dict(zip(data.txns.bank_txn_id, data.txns.entity))
    by_entity: dict[str, Counter] = {}
    for t, how in eng.how.items():
        by_entity.setdefault(ent.get(t, "?"), Counter())[how] += 1
    res = tot = 0
    for a in eng.advices:
        for ln in a["lines"]:
            tot += 1
            res += eng._adv_doc(a, ln) is not None
    stats = {"lines": len(data.txns), "by_stage": dict(Counter(eng.how.values())),
             "by_entity_stage": {e: dict(c) for e, c in sorted(by_entity.items())},
             "advices": len(eng.advices), "advices_used": len(eng.adv_used),
             "advice_lines": tot, "advice_lines_resolved": res, "masters": str(data.masters_dir),
             "fx_max_date": str(data.fx.date.max()), "entities": sorted(data.entities.entity.unique())}
    Path(str(out) + ".stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")


def cmd_score(a) -> int:
    root = Path(a.data)
    d = D.find_phase_dir(root, a.phase)
    txns = D.read(d / "bank_transactions.csv.gz")
    cats = list(D.read(D.find_masters(root, a.phase) / "categories.csv").category)
    golden = d / "golden_allocations.csv.gz"
    _print_score(score(Path(a.file), golden if golden.exists() else None, list(txns.bank_txn_id), cats))
    return 0


def main(argv=None) -> int:
    # set iteration order depends on string hashing: pin the seed so every run is reproducible
    if os.environ.get("PYTHONHASHSEED") != "0":
        import subprocess
        env = dict(os.environ, PYTHONHASHSEED="0")
        return subprocess.call([sys.executable, "-m", "bankrec.cli"] + list(sys.argv[1:] if argv is None else argv), env=env)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="bankrec")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("train-nonitem")
    s.add_argument("--data", default=DEFAULT_DATA)
    s.add_argument("--exclude-sample", action="store_true", help="hold out the sample month (honest sample scores)")
    s.add_argument("--exclude-month", default="", help="YYYY-MM held out -> cache/nonitem_ex_YYYY-MM.json (used by run --month)")
    s.set_defaults(fn=cmd_train_nonitem)
    s = sub.add_parser("run")
    s.add_argument("--data", default=DEFAULT_DATA)
    s.add_argument("--phase", default="sample", choices=D.PHASES)
    s.add_argument("--out")
    s.add_argument("--note", default="")
    s.add_argument("--no-log", action="store_true", help="do not append to bankrec_scoreboard.md (autoresearch runs)")
    s.add_argument("--month", default="", help="YYYY-MM: evaluate on one TRAIN month with its golden (dev)")
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
