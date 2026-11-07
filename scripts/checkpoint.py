"""Upload checkpoint: show the internal assessment of a file and ask the human before anything is sent.

Used by scripts/pipeline.py (LIVE) and scripts/final_run.py (FINAL). Can also be run on its own:

    python scripts/checkpoint.py live  submissions/live_v15.csv.gz
    python scripts/checkpoint.py final submissions/final_v1.csv.gz

Assessment shown:
  - predicted platform score = best kept TRAIN-eval mean (autoresearch/results.tsv) - median recent TRAIN->LIVE gap
    (bankrec_calibration.tsv); for an engine already scored on LIVE, that LIVE score is shown too;
  - FINAL: readiness-gate verdict (scripts/final_gate.py) and a widened range for unseen drift;
  - the decision: LIVE asks "upload? [y/N]"; FINAL requires typing UPLOAD (one scored submission only).
If the answer is no, it prints how to improve before uploading.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def _rows(path: Path, delim: str = "\t") -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=delim))


def assess(phase: str, file: str, gate_text: str | None = None) -> dict:
    kept = [r for r in _rows(ROOT / "autoresearch" / "results.tsv") if r.get("status") == "keep"]
    cal = _rows(ROOT / "bankrec_calibration.tsv")
    gaps = sorted(float(r["eval_mean"]) - float(r["live_score"]) for r in cal[-2:]) if cal else []
    gap = gaps[len(gaps) // 2] if gaps else 0.0
    best_eval = max((float(r["score_mean"]) for r in kept), default=None)
    pred = best_eval - gap if best_eval is not None else None
    last_live = max((float(r["live_score"]) for r in cal), default=None)
    out = {"phase": phase, "file": file, "best_eval": best_eval, "gap": gap, "predicted": pred, "best_live": last_live}
    if phase == "final":
        if gate_text is None:
            g = subprocess.run([PY, str(ROOT / "scripts" / "final_gate.py"), "check", "final", file], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            gate_text = g.stdout
        verdict = next((ln.split(":", 1)[1].strip() for ln in gate_text.splitlines() if ln.startswith("GATE:")), "UNKNOWN")
        out["gate"] = verdict
        out["flags"] = [ln.strip() for ln in gate_text.splitlines() if ln.strip().startswith("[")]
    return out


def show(a: dict) -> None:
    print("\n" + "=" * 72)
    print(f"  UPLOAD CHECKPOINT · {a['phase'].upper()} · {a['file']}")
    print("=" * 72)
    if a["predicted"] is not None:
        print(f"  internal TRAIN-month evaluation (best kept)   : {a['best_eval']:.2f}")
        print(f"  calibrated TRAIN->LIVE gap (recent uploads)  : {a['gap']:+.2f}")
        print(f"  predicted platform score                     : ~{a['predicted']:.2f}")
    if a["best_live"] is not None:
        print(f"  best LIVE score so far                       : {a['best_live']:.2f}")
    if a["phase"] == "final":
        print(f"  FINAL readiness gate                         : {a.get('gate')}")
        for f in a.get("flags", []):
            print(f"      {f}")
        if a["predicted"] is not None:
            print(f"  expected FINAL range (unseen drift)          : ~{a['predicted'] - 2.0:.1f} .. {a['predicted'] + 0.3:.1f}")
        print("  REMINDER: FINAL = ONE scored submission. The first valid upload is the score.")
    print("=" * 72)


IMPROVE = """Not uploading. Ways to improve first:
  1. .venv/Scripts/python scripts/live_report.py                 (where LIVE loses lines vs TRAIN)
  2. .venv/Scripts/python scripts/analyst.py hypotheses          (Claude: next experiments)
  3. make one change, git commit -m "exp: ...", then
     .venv/Scripts/python scripts/autoresearch.py eval "..."     (keep / revert automatically)
  4. rebuild the file and come back to this checkpoint."""


def ask(a: dict) -> bool:
    show(a)
    if not sys.stdin.isatty():
        print("non-interactive terminal: NOT uploading (run it in a terminal, or pass --yes for LIVE)")
        return False
    if a["phase"] == "final":
        if a.get("gate") == "FAIL":
            print("Gate is FAIL: fix first (docs/BANKREC_GUIDE.md §11). Type OVERRIDE to upload anyway.")
            ok = input("> ").strip() == "OVERRIDE"
        else:
            ok = input("Do you agree with this assessment and want to upload the FINAL file? Type UPLOAD to confirm: ").strip() == "UPLOAD"
    else:
        ok = input("Do you agree with this assessment and want to upload this file to the LIVE leaderboard? [y/N]: ").strip().lower() in ("y", "yes")
    if not ok:
        print(IMPROVE)
    return ok


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 3 or sys.argv[1] not in ("live", "final"):
        sys.exit("usage: python scripts/checkpoint.py live|final FILE")
    show(assess(sys.argv[1], sys.argv[2]))
