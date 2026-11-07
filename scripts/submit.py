"""Submit a file to the Talky hackathon platform and fetch the scored result (organisers' 4-step API flow).

    python scripts/submit.py LIVE submissions/live_v9.csv.gz --note "v9 drift fix"
    python scripts/submit.py FINAL submissions/final_v1.csv.gz --i-confirm-final-is-one-shot

Token: HK_TOKEN from the environment, else an `HK_TOKEN=...` line in the git-ignored `.env`. It is never printed,
logged or written anywhere by this script. Guards, in order:
  1. local official validation of the file against the phase's bank lines ("ok": true or nothing is sent);
  2. LIVE: >= 3 min since our last upload and < 20 uploads today (platform history);
  3. FINAL: needs --i-confirm-final-is-one-shot AND refuses if a FINAL submission is already scored;
  4. upload-url -> S3 POST -> score -> poll every 5 s (the venue shares one IP), max 10 min.
The scored result is saved to submissions/results/<file>__<sub_id>.json and a line goes to bankrec_scoreboard.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
API = "https://hackathon.usetalky.com/api"
DONE = {"scored", "received", "invalid", "error"}
RESULTS = ROOT / "submissions" / "results"
MIN_INTERVAL_S, DAILY_LIMIT, MAX_MB = 180, 20, 60


def token() -> str:
    t = os.environ.get("HK_TOKEN", "")
    env = ROOT / ".env"
    if not t and env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "HK_TOKEN":
                t = v.strip().strip("'\"")
    if not t:
        sys.exit("HK_TOKEN not set: export it (read -rs HK_TOKEN && export HK_TOKEN) or add HK_TOKEN=... to .env")
    return t


def validate(phase: str, path: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from bankrec import data as D
    from bankrec.submission import score
    root = ROOT / "data" / "valtermia"
    d = D.find_phase_dir(root, phase.lower())
    txns = D.read(d / "bank_transactions.csv.gz")
    cats = list(D.read(D.find_masters(root, phase.lower()) / "categories.csv").category)
    res = score(path, None, list(txns.bank_txn_id), cats)
    if not res["ok"]:
        print(json.dumps(res["errors"][:10], indent=1))
        sys.exit("local validation FAILED: nothing sent")
    print(f"local validation ok ({len(txns)} bank lines)")


def history(h: dict) -> list[dict]:
    try:
        r = requests.get(f"{API}/submissions", headers=h, timeout=30)
        if r.ok:
            j = r.json()
            return j if isinstance(j, list) else j.get("submissions", j.get("items", []))
    except requests.RequestException:
        pass
    return []


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def guards(phase: str, h: dict, confirm_final: bool) -> None:
    subs = history(h)
    mine = [s for s in subs if str(s.get("phase", "")).upper() == phase]
    if phase == "FINAL":
        if not confirm_final:
            sys.exit("FINAL is ONE scored submission: re-run with --i-confirm-final-is-one-shot")
        if any(s.get("status") == "scored" for s in mine):
            sys.exit("a FINAL submission is already scored: refusing to send another")
        return
    created = sorted(_ts(s.get("created_at") or s.get("submitted_at") or "") for s in mine)
    if created and time.time() - created[-1] < MIN_INTERVAL_S:
        sys.exit(f"last LIVE upload {time.time() - created[-1]:.0f}s ago: wait until {MIN_INTERVAL_S}s")
    today = datetime.now(timezone.utc).date()
    n_today = sum(1 for c in created if datetime.fromtimestamp(c, timezone.utc).date() == today)
    if n_today >= DAILY_LIMIT:
        sys.exit(f"{n_today} LIVE uploads today: daily limit {DAILY_LIMIT} reached")
    print(f"LIVE uploads today (platform history): {n_today}/{DAILY_LIMIT}")


def submit(phase: str, path: Path, h: dict) -> dict:
    name = path.name
    r = requests.post(f"{API}/submissions/upload-url", headers=h, json={"phase": phase, "filename": name}, timeout=30)
    if r.status_code != 201:
        sys.exit(f"upload-url failed: {r.status_code} {r.text[:500]}")
    up = r.json()
    sub_id = up["sub_id"]
    print(f"slot reserved: {sub_id}")
    with open(path, "rb") as f:
        s3 = requests.post(up["upload"]["url"], data=up["upload"]["fields"], files={"file": (name, f)}, timeout=900)
    if s3.status_code not in (200, 201, 204):
        sys.exit(f"S3 upload failed: {s3.status_code} {s3.text[:500]}")
    print("uploaded")
    r = requests.post(f"{API}/submissions/{sub_id}/score", headers=h, json={}, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"score failed: {r.status_code} {r.text[:500]}")
    sub, deadline = r.json(), time.time() + 600
    while sub.get("status") not in DONE and time.time() < deadline:
        time.sleep(5)
        sub = requests.get(f"{API}/submissions/{sub_id}", headers=h, timeout=30).json()
        print(f"  status: {sub.get('status')}")
    return sub


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["LIVE", "FINAL"])
    ap.add_argument("file")
    ap.add_argument("--note", default="")
    ap.add_argument("--i-confirm-final-is-one-shot", dest="confirm_final", action="store_true")
    ap.add_argument("--skip-validation", action="store_true", help="never use for FINAL")
    a = ap.parse_args()
    path = Path(a.file)
    if not path.is_file():
        sys.exit(f"no such file: {path}")
    if path.stat().st_size > MAX_MB * 1024 * 1024:
        sys.exit(f"file is over {MAX_MB} MB")
    if not a.skip_validation or a.phase == "FINAL":
        validate(a.phase, path)
    h = {"Authorization": f"Bearer {token()}"}
    guards(a.phase, h, a.confirm_final)
    sub = submit(a.phase, path, h)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{path.name}__{sub.get('sub_id', 'unknown')}.json"
    out.write_text(json.dumps(sub, indent=1, ensure_ascii=False), encoding="utf-8")
    score = sub.get("score")
    print(f"status {sub.get('status')}  score {score}  -> {out.relative_to(ROOT)}")
    with open(ROOT / "bankrec_scoreboard.md", "a", encoding="utf-8") as fh:
        fh.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {a.phase.lower()} UPLOAD | {path.as_posix()} | "
                 f"{score if score is not None else sub.get('status')} (platform) | {a.note} |\n")
    return 0 if sub.get("status") == "scored" else 1


if __name__ == "__main__":
    raise SystemExit(main())
