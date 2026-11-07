"""Compare platform (LIVE) results per segment: vs the previous upload and vs our TRAIN-month accuracy.

    python scripts/live_report.py                       # latest two results in submissions/results/
    python scripts/live_report.py NEW.json [OLD.json]   # explicit files (.json from submit.py, or a pasted .txt page)
    python scripts/live_report.py --train autoresearch/runs/<commit>_2025-03.log ...

Ranks segments by LIVE lines lost (n x (1 - accuracy)) and shows the gap to TRAIN, so segments that fail only on LIVE
(drift) stand out. This is the "Observe" step of the agent loop for LIVE-only behaviour.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "submissions" / "results"


def _find_segments(obj):
    """Locate {segment: {n, accuracy}} anywhere in a platform JSON."""
    if isinstance(obj, dict):
        seg = obj.get("segments") or obj.get("by_segment")
        if isinstance(seg, dict) and seg:
            return {k: (int(v.get("n", v.get("count", 0))), float(v.get("accuracy", v.get("acc", 0)))) for k, v in seg.items()}
        if isinstance(seg, list) and seg:
            return {d.get("segment") or d.get("name"): (int(d.get("n", d.get("count", 0))), float(d.get("accuracy", 0)))
                    for d in seg}
        for v in obj.values():
            r = _find_segments(v)
            if r:
                return r
    return {}


def load(path: str) -> tuple[str, float | None, dict]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".json":
        j = json.loads(text)
        score = j.get("score") if isinstance(j, dict) else None
        return p.name, score, _find_segments(j)
    # pasted result page: "segment    ~42.000    \n90,4 %"
    segs = {}
    for m in re.finditer(r"^([a-z0-9_]+)\s+~?([\d.]+)\s*\n?\s*([\d,]+)\s*%", text, re.M):
        segs[m.group(1)] = (int(m.group(2).replace(".", "")), float(m.group(3).replace(",", ".")) / 100)
    sm = re.search(r"Puntuaci[oó]n\s*\n+\s*([\d.,]+)", text)
    return p.name, float(sm.group(1).replace(",", ".")) if sm else None, segs


def train_segments(logs: list[str]) -> dict:
    acc: dict[str, list[float]] = {}
    for f in logs:
        for m in re.finditer(r"^\s+(\S+)\s+n=\s*(\d+) acc=([\d.]+)", Path(f).read_text(encoding="utf-8"), re.M):
            acc.setdefault(m.group(1), []).append(float(m.group(3)))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tlogs = sys.argv[sys.argv.index("--train") + 1:] if "--train" in sys.argv else []
    files = [a for a in args if a not in tlogs]
    if not files:
        files = sorted(glob.glob(str(RESULTS / "*")), key=lambda f: Path(f).stat().st_mtime)[-2:][::-1]
    if not tlogs:   # latest kept autoresearch runs
        tlogs = sorted(glob.glob(str(ROOT / "autoresearch" / "runs" / "*_20??-??.log")), key=lambda f: Path(f).stat().st_mtime)[-2:]
    new = load(files[0])
    old = load(files[1]) if len(files) > 1 else (None, None, {})
    tr = train_segments(tlogs)
    print(f"NEW {new[0]} score {new[1]}   OLD {old[0]} score {old[1]}   TRAIN logs: {[Path(t).name for t in tlogs]}")
    rows = []
    for s, (n, a) in new[2].items():
        o = old[2].get(s, (None, None))[1]
        t = tr.get(s)
        rows.append((n * (1 - a), s, n, a, o, t))
    print(f"{'segment':28s} {'n':>6s} {'LIVE':>6s} {'Δprev':>6s} {'TRAIN':>6s} {'gap':>6s} {'lost':>6s}")
    for lost, s, n, a, o, t in sorted(rows, reverse=True)[:30]:
        dp = f"{100 * (a - o):+6.1f}" if o is not None else "     -"
        tt = f"{100 * t:6.1f}" if t is not None else "     -"
        gap = f"{100 * (t - a):+6.1f}" if t is not None else "     -"
        print(f"{s:28s} {n:6d} {100 * a:6.1f} {dp} {tt} {gap} {lost:6.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
