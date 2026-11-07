"""End-to-end LIVE pipeline: build -> validate -> (upload) -> fetch score -> per-segment report.

    python scripts/pipeline.py --tag v10 --note "what changed"            # run LIVE + local validation + report
    python scripts/pipeline.py --tag v10 --note "..." --submit            # ... and upload, wait for the score
    python scripts/pipeline.py --file submissions/live_v9.csv.gz --submit  # upload an existing validated file

Steps and their guards:
  1. code state: the commit is recorded in the note; a dirty engine (uncommitted src/bankrec changes) is refused
  2. `bankrec run --phase live` (official validation inside; stops unless "ok": true)
  3. checkpoint (scripts/checkpoint.py): predicted score + 'upload? [y/N]'; then `scripts/submit.py LIVE`
     (re-validates, 3-min spacing, daily quota, saves the scored JSON)   [--submit only; --yes skips the question]
  4. `scripts/live_report.py` (new vs previous upload vs TRAIN months, ranked by lines lost)
FINAL is deliberately NOT automated here: follow docs/BANKREC_GUIDE.md §11 and submit.py's explicit FINAL flag.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def sh(*cmd: str) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(list(cmd), cwd=ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="version tag, e.g. v10 -> submissions/live_v10.csv.gz")
    ap.add_argument("--file", help="skip the build and use this file")
    ap.add_argument("--note", default="")
    ap.add_argument("--submit", action="store_true", help="upload after the checkpoint asks you (y/N)")
    ap.add_argument("--yes", action="store_true", help="skip the checkpoint question (unattended agent runs, LIVE only)")
    a = ap.parse_args()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if a.file:
        out = Path(a.file)
    else:
        if not a.tag:
            sys.exit("--tag or --file is required")
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src/bankrec"], cwd=ROOT, capture_output=True,
                               text=True).stdout.strip()
        if dirty:
            sys.exit("uncommitted engine changes: commit first so the LIVE file maps to a commit")
        out = Path("submissions") / f"live_{a.tag}.csv.gz"
        if sh(PY, "-m", "bankrec.cli", "run", "--phase", "live", "--out", str(out),
              "--note", f"LIVE {a.tag} = {commit} {a.note}".strip()) != 0:
            sys.exit("LIVE run failed or did not validate: nothing uploaded")
    if a.submit:
        sys.path.insert(0, str(ROOT / "scripts"))
        import checkpoint
        if a.yes:
            checkpoint.show(checkpoint.assess("live", str(out)))
        if a.yes or checkpoint.ask(checkpoint.assess("live", str(out))):
            if sh(PY, str(ROOT / "scripts" / "submit.py"), "LIVE", str(out), "--note", f"{commit} {a.note}".strip()) != 0:
                print("upload did not end in 'scored' (see above)")
    sh(PY, str(ROOT / "scripts" / "live_report.py"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
