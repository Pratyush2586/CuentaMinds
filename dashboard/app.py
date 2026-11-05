"""closeops demo dashboard. Run: streamlit run dashboard/app.py

Reads only exported files from a run dir; the one write path is record_decision.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard.data import (latest_run, load_run, reconciled_pct, record_decision,  # noqa: E402
                            source_rows)

st.set_page_config(page_title="closeops", layout="wide")
st.title("closeops - autonomous month-end close")

# ---- sidebar -------------------------------------------------------------------------
sb = st.sidebar
raw_dir = Path(sb.text_input("Raw data dir", "data/demo"))
mapping = Path(sb.text_input("Mapping", "config/mappings.synth.yaml"))
policy_path = Path(sb.text_input("Policy", "config/policy.yaml"))
results_dir = Path(sb.text_input("Results dir", "results"))
use_agents = sb.toggle("Agents", value=False, help="Off = rules only (kill switch)")

if sb.button("Generate demo data"):
    if raw_dir.is_dir() and any(raw_dir.iterdir()):
        sb.info(f"{raw_dir} already has files; not overwriting.")
    else:
        from closeops.synth.generate import generate
        with st.spinner("Generating synthetic data"):
            generate(raw_dir, seed=7)
        sb.success(f"Generated into {raw_dir}")

if sb.button("Run close", type="primary"):
    from closeops.pipeline import run_close
    with st.status("Running close...", expanded=True) as status:
        try:
            st.write("Ingesting, checking, tiering, exporting...")
            res = run_close(raw_dir, mapping, policy_path, results_dir, use_agents=use_agents)
            for ev in load_run(res.run_dir)["events"]:
                if ev["actor"] == "pipeline":
                    st.write(f"[{ev['action']}] {json.dumps(ev['output'], default=str)[:160]}")
            st.session_state["run_dir"] = str(res.run_dir)
            status.update(label=f"Close complete: {res.run_dir.name}", state="complete")
        except Exception as e:  # show the failure instead of a stack trace in the demo
            status.update(label=f"Close failed: {e}", state="error")

if "run_dir" not in st.session_state:
    newest = latest_run(results_dir)
    if newest is not None:
        st.session_state["run_dir"] = str(newest)
if "run_dir" not in st.session_state:
    st.info("No run yet. Generate demo data if needed, then press **Run close** in the sidebar.")
    st.stop()

run_dir = Path(st.session_state["run_dir"])
run = load_run(run_dir)
findings, payments = run["findings"], run["payments"]
sb.caption(f"Run: {run_dir.name}")


def _decision_buttons(item_id: str) -> None:
    c1, c2, _ = st.columns([1, 1, 6])
    for col, dec in ((c1, "approve"), (c2, "reject")):
        if col.button(dec.capitalize(), key=f"{dec}_{item_id}"):
            record_decision(run_dir, item_id, dec)
            st.rerun()


tabs = st.tabs(["Overview", "Exceptions", "Journal & Payments", "Close report", "Human review"])

# ---- overview ------------------------------------------------------------------------
with tabs[0]:
    hold = int((findings["tier"] == "HOLD").sum()) if not findings.empty else 0
    pending = int((findings["display_tier"] == "HOLD").sum()) if not findings.empty else 0
    pct = reconciled_pct(run)
    k = st.columns(4)
    k[0].metric("Findings", len(findings))
    k[1].metric("HOLD findings", hold, help=f"{pending} still awaiting human review")
    k[2].metric("Bank reconciled", f"{pct:.1f}%" if pct is not None else "n/a")
    k[3].metric("Evidence chain", "verified ✓" if run["evidence_ok"] else "BROKEN ✗")
    if not findings.empty:
        st.subheader("Findings by category")
        st.bar_chart(findings["category"].value_counts().rename_axis("category").reset_index(name="count"), x="category", y="count")
        st.subheader("Findings by tier")
        st.dataframe(findings["display_tier"].value_counts().rename("count"))

# ---- exceptions ----------------------------------------------------------------------
with tabs[1]:
    if findings.empty:
        st.write("No findings.")
    else:
        c1, c2 = st.columns(2)
        cats = c1.multiselect("Category", sorted(findings["category"].unique()))
        tiers = c2.multiselect("Tier", sorted(findings["display_tier"].unique()))
        view = findings
        if cats:
            view = view[view["category"].isin(cats)]
        if tiers:
            view = view[view["display_tier"].isin(tiers)]
        st.dataframe(view[["finding_id", "check", "category", "severity", "entity",
                           "amount_eur", "display_tier", "explanation"]],
                     hide_index=True, use_container_width=True)
        if not view.empty:
            fid = st.selectbox("Drill into finding", list(view["finding_id"]))
            row = view[view["finding_id"] == fid].iloc[0]
            st.markdown(f"**{row['check']}** - {row['explanation']}")
            st.caption(f"Resolved by: {row['resolved_by'] or 'rules'} | tier {row['display_tier']}")
            st.subheader("Evidence")
            st.json([e for e in run["events"]
                     if fid in e["inputs"] or e["output"].get("finding_id") == fid][:10])
            st.subheader("Source rows")
            src = source_rows(raw_dir, list(row["source_refs"]))
            if src.empty:
                st.write("No source rows found (check the raw data dir in the sidebar).")
            else:
                st.dataframe(src, hide_index=True, use_container_width=True)

# ---- journal & payments --------------------------------------------------------------
with tabs[2]:
    st.subheader("Proposed journal entries")
    st.dataframe(run["journal"], hide_index=True, use_container_width=True)
    st.subheader("Proposed payments")
    if payments.empty:
        st.write("No payment proposals.")
    else:
        n_blocked = int((payments["tier"] == "HOLD").sum())
        if n_blocked:
            st.warning(f"{n_blocked} payment(s) blocked (unverified/changed IBAN or fraud hold)")
        st.dataframe(payments.style.apply(
            lambda r: ["background-color: #ffd6d6" if r["tier"] == "HOLD" else "" for _ in r],
            axis=1), hide_index=True, use_container_width=True)

# ---- report --------------------------------------------------------------------------
with tabs[3]:
    st.markdown(run["report"] or "No report.")

# ---- human review --------------------------------------------------------------------
with tabs[4]:
    st.caption("Decisions are appended to the tamper-evident evidence log as actor 'human'.")
    todo = findings[findings["display_tier"] == "HOLD"] if not findings.empty else findings
    st.subheader(f"HOLD findings ({len(todo)} pending)")
    for r in todo.itertuples():
        st.markdown(f"**{r.finding_id}** - {r.check} ({r.entity}) - {r.explanation}")
        _decision_buttons(r.finding_id)
    held = payments[payments["display_tier"] == "HOLD"] if not payments.empty else payments
    st.subheader(f"HOLD payments ({len(held)} pending)")
    for r in held.itertuples():
        st.markdown(f"**{r.invoice_id}** - {r.amount} {r.currency} to {r.iban} - {r.block_reason}")
        _decision_buttons(r.invoice_id)
    if run["decisions"]:
        st.subheader("Decisions")
        st.dataframe(pd.Series(run["decisions"], name="decision"))
