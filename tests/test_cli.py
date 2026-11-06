import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from closeops.cli import main
from closeops.synth.generate import generate

ROOT = Path(__file__).resolve().parent.parent
MAPPING = ROOT / "config" / "mappings.synth.yaml"


def test_profile_piped_stdout_handles_any_unicode(tmp_path):
    # Build-day data may hold names outside the Windows console code page (cp1252).
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "vendors.csv").write_text("id,name\n1,Łódź Dostawy Sp. z o.o.\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    proc = subprocess.run([sys.executable, "-m", "closeops.cli", "profile", str(raw)],
                          capture_output=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "Łódź Dostawy" in proc.stdout.decode("utf-8")


def test_score_prints_by_check(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    findings = [{"check": "dup_exact", "category": "duplicate", "record_type": "invoice",
                 "record_ids": ["D1", "D0"]},
                {"check": "tax_rate", "category": "tax", "record_type": "invoice",
                 "record_ids": ["D7"]}]
    (run_dir / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
    key = tmp_path / "key.json"
    key.write_text(json.dumps([{"category": "duplicate", "check": "dup_exact", "record_id": "D1"},
                               {"category": "tax", "check": "tax_amount", "record_id": "D9"}]),
                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # scoreboard.md is written to the cwd
    assert main(["score", str(run_dir), str(key), "--note", "t"]) == 0
    out = capsys.readouterr().out
    lines = {ln.split()[0]: ln.split()[1:] for ln in out.splitlines() if ln.strip()}
    assert "by check:" in out
    assert lines["tax_rate"][3:] == ["0", "1", "0"]    # tp fp fn: a false positive
    assert lines["tax_amount"][3:] == ["0", "0", "1"]  # a miss
    assert (tmp_path / "scoreboard.md").exists()


def test_run_prints_notes(tmp_path, capsys):
    raw = generate(tmp_path / "raw", seed=7, n_invoices=200)
    cfg = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    del cfg["tables"]["tax_rates"]
    mapping = tmp_path / "m.yaml"
    mapping.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert main(["run", str(raw), "--mapping", str(mapping), "--policy",
                 str(ROOT / "config" / "policy.yaml"), "--out", str(tmp_path / "r")]) == 0
    assert "note: skipped tax_rate: no tax_rates" in capsys.readouterr().out


def test_synth_hard_flag(tmp_path):
    assert main(["synth", str(tmp_path / "h"), "--seed", "3", "--n", "200", "--hard"]) == 0
    key = json.loads((tmp_path / "h" / "answer_key.json").read_text(encoding="utf-8"))
    generate(tmp_path / "n", seed=3, n_invoices=200)
    normal = json.loads((tmp_path / "n" / "answer_key.json").read_text(encoding="utf-8"))
    assert len(key) == len(normal) + 5


def test_load_env_sets_missing_only(tmp_path, monkeypatch):
    from closeops.cli import load_env
    (tmp_path / ".env").write_text("# keys\nA_KEY=from-file\nB_KEY = 'quoted'\n\nbad line\n",
                                   encoding="utf-8")
    monkeypatch.setenv("A_KEY", "already-set")
    monkeypatch.delenv("B_KEY", raising=False)
    load_env(tmp_path / ".env")
    import os
    assert os.environ["A_KEY"] == "already-set" and os.environ["B_KEY"] == "quoted"
    load_env(tmp_path / "missing.env")  # absent file is fine
