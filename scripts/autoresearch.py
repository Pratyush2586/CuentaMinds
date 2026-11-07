"""Autoresearch harness for the bankrec engine (after karpathy/autoresearch). READ-ONLY during the loop.

The agent edits the engine, commits, then runs:

    python scripts/autoresearch.py eval "short description"     # evaluate HEAD, decide keep/discard, log, reset
    python scripts/autoresearch.py baseline                      # first run: evaluate HEAD as the baseline
    python scripts/autoresearch.py holdout                       # check the hold-out month (only before merging)
    python scripts/autoresearch.py status                        # print results.tsv

Metric: mean official score over the DEV months (TRAIN months with the full open book, category model trained without
that month). Keep rule: mean improves by >= MIN_GAIN and no dev month drops by more than MAX_DROP (anti-overfit).
On discard the experiment commit is undone (git reset --soft HEAD~1 + restore the engine files); results.tsv is
git-ignored so it survives. See docs/AUTORESEARCH.md for the full protocol.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_MONTHS = ["2025-03", "2024-10"]
HOLDOUT_MONTH = "2025-05"
MIN_GAIN = 0.02
MAX_DROP = 0.30
TIMEOUT_S = 12 * 60          # a month must match well within this, or LIVE/FINAL (~7 months) gets too slow
OUT = ROOT / "autoresearch"
TSV = OUT / "results.tsv"
COLS = ["commit", "score_mean", *[f"score_{m}" for m in DEV_MONTHS], "match_s", "status", "description"]
EDITABLE = ["src/bankrec/engine.py", "src/bankrec/text.py", "src/bankrec/advices.py"]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else str(ROOT / ".venv" / "bin" / "python")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def ensure_models(months: list[str]) -> None:
    for m in months:
        if not (ROOT / "data" / "valtermia" / "cache" / f"nonitem_ex_{m}.json").exists():
            subprocess.run([PY, "-m", "bankrec.cli", "train-nonitem", "--exclude-month", m], cwd=ROOT, check=True)


def run_month(month: str, tag: str) -> dict:
    out = OUT / "runs" / f"{tag}_{month}.csv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    log = OUT / "runs" / f"{tag}_{month}.log"
    t0 = time.time()
    try:
        with open(log, "w", encoding="utf-8") as fh:
            p = subprocess.run([PY, "-m", "bankrec.cli", "run", "--month", month, "--out", str(out), "--no-log"],
                               cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, timeout=TIMEOUT_S,
                               env=dict(os.environ, PYTHONHASHSEED="0"))
    except subprocess.TimeoutExpired:
        return {"month": month, "status": "timeout", "score": 0.0, "match_s": time.time() - t0, "log": str(log)}
    text = log.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'"score":\s*([\d.]+)', text)
    ms = re.search(r"match (\d+)s", text)
    ok = p.returncode == 0 and m is not None and '"ok": true' in text
    return {"month": month, "status": "ok" if ok else "crash", "score": float(m.group(1)) if ok else 0.0,
            "match_s": int(ms.group(1)) if ms else 0, "log": str(log), "sub": str(out), "text": text}


def diagnostics(r: dict) -> str:
    """Observation for the agent: metrics, per-exception F1 and where pairs are lost (dev month 1)."""
    if r["status"] != "ok":
        return "\n".join(r.get("text", "").splitlines()[-30:])
    mt = re.search(r'"metrics": \{(.*?)\}', r["text"], re.S)
    lines = ["metrics: " + " ".join(x.strip() for x in (mt.group(1).split(",") if mt else []) if x.strip())]
    for script in ("bankrec_exc.py", "bankrec_pairs.py"):
        p = subprocess.run([PY, str(ROOT / "scripts" / script), r["sub"], "--month", r["month"]], cwd=ROOT,
                           capture_output=True, text=True)
        lines += p.stdout.splitlines()[:16]
    return "\n".join(lines)


def read_tsv() -> list[dict]:
    if not TSV.exists():
        return []
    with open(TSV, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def append(row: dict) -> None:
    new = not TSV.exists()
    TSV.parent.mkdir(parents=True, exist_ok=True)
    with open(TSV, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, delimiter="\t")
        if new:
            w.writeheader()
        w.writerow(row)


def evaluate(months: list[str], tag: str) -> list[dict]:
    ensure_models(months)
    with ThreadPoolExecutor(len(months)) as ex:
        return list(ex.map(lambda m: run_month(m, tag), months))


def summary(commit: str, res: list[dict]) -> dict:
    ok = all(r["status"] == "ok" for r in res)
    mean = round(sum(r["score"] for r in res) / len(res), 3) if ok else 0.0
    print("---")
    print(f"commit:      {commit}")
    print(f"score_mean:  {mean:.3f}")
    for r in res:
        print(f"score_{r['month']}: {r['score']:.2f}  ({r['status']}, match {r['match_s']}s)")
    return {"commit": commit, "score_mean": f"{mean:.3f}", **{f"score_{r['month']}": f"{r['score']:.2f}" for r in res},
            "match_s": str(max(r["match_s"] for r in res)), "status": "ok" if ok else "crash"}


def cmd_eval(desc: str, baseline: bool = False) -> int:
    commit = git("rev-parse", "--short", "HEAD")
    if git("status", "--porcelain", "--", *EDITABLE):
        print("commit your engine change first (git commit), then run eval")
        return 2
    kept = [r for r in read_tsv() if r["status"] == "keep"]
    if not baseline and not kept:
        print("no baseline yet: run `python scripts/autoresearch.py baseline` first")
        return 2
    res = evaluate(DEV_MONTHS, commit)
    row = summary(commit, res) | {"description": desc}
    if baseline:
        row["status"] = "keep" if row["status"] == "ok" else "crash"
    elif row["status"] == "ok":
        best = kept[-1]
        gain = float(row["score_mean"]) - float(best["score_mean"])
        drops = [float(best[f"score_{m}"]) - float(row[f"score_{m}"]) for m in DEV_MONTHS]
        keep = gain >= MIN_GAIN and max(drops) <= MAX_DROP
        row["status"] = "keep" if keep else "discard"
        print(f"vs best {best['commit']} ({best['score_mean']}): gain {gain:+.3f}, worst month drop {max(drops):+.2f}"
              f" -> {row['status'].upper()}")
    append(row)
    print("--- observation (dev month 1) ---")
    print(diagnostics(res[0]))
    if row["status"] in ("discard", "crash") and not baseline:
        if git("log", "-1", "--format=%s").startswith("exp:"):   # undo the experiment commit, keep other files
            git("reset", "--soft", "HEAD~1")
            git("checkout", "HEAD", "--", *EDITABLE)
            git("reset", "-q")
            print(f"reverted to {git('rev-parse', '--short', 'HEAD')}")
        else:
            print("NOTE: HEAD is not an 'exp:' commit; nothing reverted, reset manually")
    return 0


def cmd_holdout() -> int:
    commit = git("rev-parse", "--short", "HEAD")
    res = evaluate([HOLDOUT_MONTH], commit + "_holdout")
    summary(commit, res)
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "baseline":
        return cmd_eval("baseline", baseline=True)
    if cmd == "eval":
        return cmd_eval(" ".join(sys.argv[2:]) or "(no description)")
    if cmd == "holdout":
        return cmd_holdout()
    for r in read_tsv():
        print("\t".join(r[c] for c in COLS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
