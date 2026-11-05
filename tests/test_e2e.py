import json
import time
from pathlib import Path

import pytest

from closeops.evidence import EvidenceLog
from closeops.pipeline import run_close
from closeops.scoring import score
from closeops.synth.generate import generate

ROOT = Path(__file__).resolve().parent.parent
MAPPING = ROOT / "config" / "mappings.synth.yaml"
POLICY = ROOT / "config" / "policy.yaml"


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    base = tmp_path_factory.mktemp("e2e")
    raw = generate(base / "raw", seed=7)
    t0 = time.time()
    res = run_close(raw, MAPPING, POLICY, base / "results")
    elapsed = time.time() - t0
    key = json.loads((raw / "answer_key.json").read_text(encoding="utf-8"))
    return res, score(res.findings, key), elapsed


def test_thresholds(scored):
    _, s, elapsed = scored
    assert elapsed < 60
    assert s["overall"]["precision"] >= 0.9 and s["overall"]["recall"] >= 0.85
    for cat in ("duplicate", "tax", "fraud"):
        assert s["by_category"][cat]["f1"] >= 0.85


def test_seed7_exact_regression(scored):
    o = scored[1]["overall"]
    assert (o["tp"], o["fp"], o["fn"]) == (90, 0, 0)


def test_evidence_and_outputs(scored):
    res, _, _ = scored
    ev = res.run_dir / "evidence.jsonl"
    assert EvidenceLog.verify(ev)
    assert len(ev.read_text(encoding="utf-8").splitlines()) == len(res.findings) + 4
    out = json.loads((res.run_dir / "findings.json").read_text(encoding="utf-8"))
    assert len(out) == len(res.findings)
    assert all(f.tier is not None for f in res.findings)


def test_agents_flag_notes(tmp_path):
    raw = generate(tmp_path / "raw", seed=7, n_invoices=200)
    res = run_close(raw, MAPPING, POLICY, tmp_path / "r", use_agents=True)
    assert "agents not available yet" in res.notes
