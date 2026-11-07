"""FINAL-readiness gate: label-free health profile of a submission, calibrated on LIVE platform scores.

    python scripts/final_gate.py calibrate                                   # LIVE files with platform scores: proxy vs score
    python scripts/final_gate.py check final submissions/final_v1.csv.gz     # FINAL vs best LIVE baseline -> PASS/WARN/FAIL
    python scripts/final_gate.py check live  submissions/live_v9.csv.gz      # any phase vs the baseline

FINAL has no golden and only one scored upload, so we cannot score it. What we can do:
  1. profile a submission without labels (per entity / side: share of lines with an allocation, UNIDENTIFIED rates,
     exception mix, amount coverage; plus the run's .stats.json: stage mix, advice-line resolution, masters, fx dates);
  2. check on LIVE that the profile tracks the real platform score (calibrate);
  3. compare FINAL's profile with the scored LIVE baseline entity by entity: a new entity (ZA01), a renamed bank or a
     broken parser shows up as an outlier BEFORE the one-shot upload, with where to look.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from bankrec import data as D  # noqa: E402

RESULTS = ROOT / "submissions" / "results"
EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]


def profile(phase: str, sub_path: str) -> dict:
    root = ROOT / "data" / "valtermia"
    tx = D.read(D.find_phase_dir(root, phase) / "bank_transactions.csv.gz")[["bank_txn_id", "entity", "amount", "booking_date"]]
    tx["amount"] = pd.to_numeric(tx.amount)
    s = D.read(sub_path)
    g = s.groupby("bank_txn_id").agg(item=("item_id", lambda x: (x != "").any()),
                                     unid_exc=("exception", lambda x: (x == "UNIDENTIFIED").any()),
                                     unid_cat=("category", lambda x: (x == "UNIDENTIFIED").any()),
                                     rows=("item_id", "size"))
    t = tx.set_index("bank_txn_id").join(g).fillna({"item": False, "unid_exc": False, "unid_cat": False, "rows": 0})
    t["side"] = t.amount.map(lambda a: "in" if a > 0 else "out")
    t["absamt"] = t.amount.abs()

    def stats(df):
        return {"n": int(len(df)), "item_share": round(float(df["item"].mean()), 4),
                "unid_exc": round(float(df.unid_exc.mean()), 4), "unid_cat": round(float(df.unid_cat.mean()), 4),
                "amount_cov": round(float(df.absamt[df["item"]].sum() / max(1e-9, df.absamt.sum())), 4),
                "rows_per_line": round(float(df.rows.mean()), 3)}
    p = {"file": Path(sub_path).name, "phase": phase, "overall": stats(t),
         "side": {k: stats(v) for k, v in t.groupby("side")},
         "entity": {k: stats(v) for k, v in t.groupby("entity")},
         "exceptions": {e: round(float((s.exception == e).sum() / len(t)), 5) for e in EXC}}
    sc = Path(sub_path + ".stats.json")
    if sc.exists():
        st = json.loads(sc.read_text(encoding="utf-8"))
        p["run"] = {"advice_resolution": round(st["advice_lines_resolved"] / max(1, st["advice_lines"]), 4),
                    "advices_used_share": round(st["advices_used"] / max(1, st["advices"]), 4),
                    "masters": st["masters"], "fx_max_date": st["fx_max_date"], "entities": st["entities"],
                    "by_stage": st["by_stage"], "by_entity_stage": st["by_entity_stage"]}
    return p


def platform_scores() -> dict[str, float]:
    out = {}
    for f in glob.glob(str(RESULTS / "*.json")):
        j = json.loads(Path(f).read_text(encoding="utf-8"))
        name = j.get("file") or j.get("filename") or Path(f).name.split("__")[0]
        if j.get("score") is not None:
            out[Path(name).name] = float(j["score"])
    return out


def cmd_calibrate() -> None:
    scores = platform_scores()
    rows = []
    for name, sc in sorted(scores.items(), key=lambda x: x[1]):
        f = ROOT / "submissions" / name
        if f.exists():
            p = profile("live", str(f))
            o = p["overall"]
            rows.append((name, sc, o["item_share"], o["unid_exc"], o["unid_cat"], o["amount_cov"],
                         p.get("run", {}).get("advice_resolution")))
    print(f"{'file':22s} {'LIVE':>6s} {'items':>6s} {'unidX':>6s} {'unidC':>6s} {'amtcov':>6s} {'advres':>6s}")
    for r in rows:
        print(f"{r[0]:22s} {r[1]:6.2f} {r[2]:6.3f} {r[3]:6.3f} {r[4]:6.3f} {r[5]:6.3f} {r[6] if r[6] is not None else '   -':>6}")
    if len(rows) >= 3:
        d = pd.DataFrame(rows, columns=["f", "score", "items", "unidX", "unidC", "amt", "adv"])
        print("correlation with LIVE score:", d[["items", "unidX", "unidC", "amt"]].corrwith(d.score).round(2).to_dict())


def baseline() -> tuple[str, float] | None:
    sc = {k: v for k, v in platform_scores().items() if (ROOT / "submissions" / k).exists()}
    return max(sc.items(), key=lambda x: x[1]) if sc else None


def cmd_check(phase: str, path: str) -> int:
    b = baseline()
    if not b:
        sys.exit("no scored LIVE baseline in submissions/results/")
    bp, cp = profile("live", str(ROOT / "submissions" / b[0])), profile(phase, path)
    flags: list[tuple[str, str]] = []
    bo, co = bp["overall"], cp["overall"]
    print(f"baseline {b[0]} (LIVE {b[1]:.2f})  vs  candidate {cp['file']} ({phase})")
    for k in ("item_share", "unid_exc", "unid_cat", "amount_cov", "rows_per_line"):
        print(f"  {k:14s} {bo[k]:8.4f} -> {co[k]:8.4f}")
    if co["item_share"] < bo["item_share"] - 0.03:
        flags.append(("WARN", f"overall item share {co['item_share']:.3f} vs {bo['item_share']:.3f} (-3pp)"))
    if co["unid_exc"] > bo["unid_exc"] * 2 + 0.01:
        flags.append(("WARN", f"UNIDENTIFIED share {co['unid_exc']:.3f} vs {bo['unid_exc']:.3f}"))
    print("  by side:", {k: (bp['side'].get(k, {}).get('item_share'), v['item_share']) for k, v in cp["side"].items()})
    print(f"  {'entity':6s} {'n':>7s} {'items(base)':>12s} {'items':>7s} {'unid(base)':>11s} {'unid':>6s}")
    for e, v in sorted(cp["entity"].items()):
        bv = bp["entity"].get(e)
        bi = f"{bv['item_share']:.3f}" if bv else "NEW"
        bu = f"{bv['unid_exc']:.3f}" if bv else "NEW"
        print(f"  {e:6s} {v['n']:7d} {bi:>12s} {v['item_share']:7.3f} {bu:>11s} {v['unid_exc']:6.3f}")
        if bv is None:
            if v["n"] >= 200 and (v["item_share"] < 0.5 or v["unid_exc"] > 0.2):
                flags.append(("FAIL", f"new entity {e}: item share {v['item_share']:.2f}, unidentified {v['unid_exc']:.2f}"))
            elif v["n"] >= 200 and v["item_share"] < bo["item_share"] - 0.15:
                flags.append(("WARN", f"new entity {e} below the overall level: {v['item_share']:.2f}"))
        elif v["n"] >= 500 and v["item_share"] < bv["item_share"] - 0.10:
            flags.append(("WARN", f"entity {e}: item share {v['item_share']:.2f} vs {bv['item_share']:.2f}"))
    for e in EXC:
        a, c = bp["exceptions"][e], cp["exceptions"][e]
        if (a > 0.0005 or c > 0.0005) and (c > 2.5 * a + 0.0005 or c < 0.4 * a - 0.0005):
            flags.append(("WARN", f"exception {e} rate {c:.4f} vs {a:.4f}"))
    run = cp.get("run")
    if run:
        print(f"  advice lines resolved {run['advice_resolution']:.3f} (baseline {bp.get('run', {}).get('advice_resolution', '-')})"
              f" · masters {run['masters']} · fx up to {run['fx_max_date']}")
        if run["advice_resolution"] < 0.95:
            flags.append(("WARN", f"advice lines resolved {run['advice_resolution']:.3f} < 0.95 (layout drift?)"))
        if phase == "final" and "masters_final" not in run["masters"]:
            flags.append(("FAIL", "FINAL run did not use masters_final"))
        if phase == "final" and run["fx_max_date"] < "2026-06-01":
            flags.append(("WARN", f"fx rates end {run['fx_max_date']} (FINAL runs to 2026-06-30)"))
    else:
        flags.append(("WARN", "no .stats.json next to the file (re-run with the current CLI for run internals)"))
    worst = "FAIL" if any(f[0] == "FAIL" for f in flags) else "WARN" if flags else "PASS"
    print(f"\nGATE: {worst}")
    for lvl, msg in flags:
        print(f"  [{lvl}] {msg}")
    est = b[1] - 100 * max(0.0, bo["item_share"] - co["item_share"]) * 0.5
    print(f"rough estimate: ~{est:.1f} (baseline LIVE score, minus half the lost item share). Drift not seen here is not covered")
    return 1 if worst == "FAIL" else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 2 and sys.argv[1] == "calibrate":
        cmd_calibrate()
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "check":
        return cmd_check(sys.argv[2], sys.argv[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
