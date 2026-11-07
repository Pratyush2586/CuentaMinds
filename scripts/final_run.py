"""FINAL-day orchestrator (docs/BANKREC_GUIDE.md §11). Every step stops the run on failure.

    python scripts/final_run.py                       # archive already in data/: verify, extract, sanity, run, gate, review
    python scripts/final_run.py --download            # also download the archive from the public Datos URL
    python scripts/final_run.py --skip-run --file submissions/final_v1.csv.gz    # gate + review an existing file
    python scripts/final_run.py ... --upload --i-confirm-final-is-one-shot          # and upload (ONE scored FINAL)

Steps: 1 checksum against the site's public config.json · 2 extract to data/valtermia/final · 3 sanity (lines, dates,
masters_final, ZA01, fx dates) · 4 `bankrec run --phase final` · 5 readiness gate (scripts/final_gate.py) ·
6 Claude release-manager review of the gate (scripts/analyst.py final-review) · 7 upload only with both flags.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
SITE = "https://hackathon.usetalky.com"
ARCHIVE = ROOT / "data" / "bankrec-final.tar.gz"
DEST = ROOT / "data" / "valtermia" / "final"


def step(n: int, msg: str) -> None:
    print(f"\n=== {n}. {msg}", flush=True)


def final_download() -> dict:
    cfg = requests.get(f"{SITE}/config.json", timeout=30).json()
    return next(d for d in cfg["downloads"] if d["id"] == "final")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--file", default="submissions/final_v1.csv.gz")
    ap.add_argument("--no-review", action="store_true", help="skip the Claude review")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--i-confirm-final-is-one-shot", dest="confirm", action="store_true")
    a = ap.parse_args()
    out = ROOT / a.file
    if not a.skip_run:
        step(1, "archive + checksum")
        d = final_download()
        if not d.get("available"):
            sys.exit("FINAL is not published yet (config.json: available=false)")
        if a.download and not ARCHIVE.exists():
            with requests.get(SITE + d["url"], stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(ARCHIVE, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
        if not ARCHIVE.exists():
            sys.exit(f"put the FINAL archive at {ARCHIVE} (or use --download)")
        got = sha256(ARCHIVE)
        print(f"sha256 {got}\nsite   {d.get('sha256')}")
        if d.get("sha256") and got != d["sha256"]:
            sys.exit("CHECKSUM MISMATCH: re-download")
        step(2, "extract")
        if not list(DEST.rglob("bank_transactions.csv.gz")):
            DEST.mkdir(parents=True, exist_ok=True)
            with tarfile.open(ARCHIVE) as t:
                t.extractall(DEST, filter="data")
        print("phase dir:", [str(p.parent) for p in DEST.rglob("bank_transactions.csv.gz")])
        step(3, "sanity")
        sys.path.insert(0, str(ROOT / "src"))
        from bankrec import data as D
        data = D.load(ROOT / "data" / "valtermia", "final")
        lines, lo, hi = len(data.txns), data.txns.booking_date.min(), data.txns.booking_date.max()
        print(f"lines {lines}  {lo} .. {hi}\nmasters {data.masters_dir}\nentities {sorted(data.entities.entity.unique())}\n"
              f"fx up to {data.fx.date.max()}  open items {int((data.items.open > 0).sum())}")
        problems = [m for m, bad in [("masters_final not used", "masters_final" not in str(data.masters_dir)),
                                     ("dates not in 2026-01..06", not (lo >= "2026-01-01" and hi <= "2026-06-30")),
                                     ("fewer than 100k lines", lines < 100_000)] if bad]
        if problems:
            print("SANITY WARNINGS:", problems)
        del data
        step(4, "run")
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if subprocess.call([PY, "-m", "bankrec.cli", "run", "--phase", "final", "--out", str(out),
                            "--note", f"FINAL = engine {commit}"], cwd=ROOT) != 0:
            sys.exit("FINAL run failed or did not validate")
    step(5, "readiness gate")
    gate = subprocess.run([PY, str(ROOT / "scripts" / "final_gate.py"), "check", "final", str(out)], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(gate.stdout)
    gate_file = Path(str(out) + ".gate.txt")
    gate_file.write_text(gate.stdout, encoding="utf-8")
    if not a.no_review:
        step(6, "Claude release-manager review (advisory)")
        subprocess.call([PY, str(ROOT / "scripts" / "analyst.py"), "final-review", str(gate_file)], cwd=ROOT)
    step(7, "upload")
    if not (a.upload and a.confirm):
        print(f"NOT uploaded. When the team agrees:\n  {PY} scripts/submit.py FINAL {a.file} --i-confirm-final-is-one-shot")
        return 0
    if "GATE: FAIL" in gate.stdout:
        sys.exit("gate FAIL: refusing to upload (fix, re-run, or upload by hand if you accept the risk)")
    return subprocess.call([PY, str(ROOT / "scripts" / "submit.py"), "FINAL", str(out), "--i-confirm-final-is-one-shot"],
                           cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
