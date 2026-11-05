"""closeops command line: profile, synth, run, score."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from closeops.ingest.profile import profile_dir
from closeops.pipeline import run_close
from closeops.scoring import score
from closeops.synth.generate import generate

SCOREBOARD = "scoreboard.md"
_HEADER = ("| time | run_dir | precision | recall | f1 | tp | fp | fn | note |\n"
           "|---|---|---|---|---|---|---|---|---|\n")


def _cmd_profile(a) -> int:
    print(profile_dir(Path(a.raw_dir)))
    return 0


def _cmd_synth(a) -> int:
    print(generate(Path(a.out_dir), seed=a.seed, n_invoices=a.n))
    return 0


def _cmd_run(a) -> int:
    res = run_close(Path(a.raw_dir), Path(a.mapping), Path(a.policy), Path(a.out),
                    use_agents=a.agents)
    tiers = Counter(f.tier.value for f in res.findings)
    summary = ", ".join(f"{t}={tiers.get(t, 0)}" for t in ("AUTO", "AUTO_NOTE", "HOLD"))
    print(res.run_dir)
    print(f"{len(res.findings)} findings ({summary})")
    return 0


def _row(label: str, m: dict) -> str:
    return (f"{label:<28} {m['precision']:>9.3f} {m['recall']:>7.3f} {m['f1']:>7.3f} "
            f"{m['tp']:>5} {m['fp']:>5} {m['fn']:>5}")


def _cmd_score(a) -> int:
    run_dir = Path(a.run_dir)
    findings = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    key = json.loads(Path(a.answer_key).read_text(encoding="utf-8"))
    res = score(findings, key)
    print(f"{'':<28} {'precision':>9} {'recall':>7} {'f1':>7} {'tp':>5} {'fp':>5} {'fn':>5}")
    print(_row("overall", res["overall"]))
    for cat, m in res["by_category"].items():
        print(_row(f"  {cat}", m))
    o = res["overall"]
    board = Path.cwd() / SCOREBOARD
    note = a.note.replace("|", "/")
    line = (f"| {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {run_dir} | {o['precision']:.3f} "
            f"| {o['recall']:.3f} | {o['f1']:.3f} | {o['tp']} | {o['fp']} | {o['fn']} | {note} |\n")
    new = not board.exists() or board.stat().st_size == 0
    with board.open("a", encoding="utf-8", newline="\n") as fh:
        if new:
            fh.write(_HEADER)
        fh.write(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="closeops")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("profile", help="profile raw files as markdown")
    s.add_argument("raw_dir")
    s.set_defaults(fn=_cmd_profile)
    s = sub.add_parser("synth", help="generate the synthetic dataset")
    s.add_argument("out_dir")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--n", type=int, default=600)
    s.set_defaults(fn=_cmd_synth)
    s = sub.add_parser("run", help="run the close")
    s.add_argument("raw_dir")
    s.add_argument("--mapping", required=True)
    s.add_argument("--policy", default="config/policy.yaml")
    s.add_argument("--out", default="results")
    s.add_argument("--agents", action="store_true")
    s.set_defaults(fn=_cmd_run)
    s = sub.add_parser("score", help="score a run against an answer key")
    s.add_argument("run_dir")
    s.add_argument("answer_key")
    s.add_argument("--note", default="")
    s.set_defaults(fn=_cmd_score)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
