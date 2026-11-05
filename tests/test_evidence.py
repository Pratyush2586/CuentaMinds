import json

from closeops.evidence import EvidenceLog


def _add(log, n):
    return log.append("rules", f"act{n}", ["a"], {"k": n}, {"o": n}, 0.9, "AUTO")


def test_chain_verify_and_tamper(tmp_path):
    p = tmp_path / "ev.jsonl"
    log = EvidenceLog(p)
    evs = [_add(log, i) for i in range(3)]
    assert [e["id"] for e in evs] == ["EV-000001", "EV-000002", "EV-000003"]
    assert evs[0]["prev_hash"] == "0" * 64 and evs[1]["prev_hash"] == evs[0]["hash"]
    assert EvidenceLog.verify(p) is True
    lines = p.read_text(encoding="utf-8").splitlines()
    d = json.loads(lines[1])
    d["actor"] = "evil"
    lines[1] = json.dumps(d)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert EvidenceLog.verify(p) is False


def test_missing_empty_malformed(tmp_path):
    assert EvidenceLog.verify(tmp_path / "none.jsonl") is True
    e = tmp_path / "e.jsonl"
    e.write_text("", encoding="utf-8")
    assert EvidenceLog.verify(e) is True
    e.write_text("not json\n", encoding="utf-8")
    assert EvidenceLog.verify(e) is False


def test_continues_numbering(tmp_path):
    p = tmp_path / "ev.jsonl"
    _add(EvidenceLog(p), 1)
    ev = _add(EvidenceLog(p), 2)
    assert ev["id"] == "EV-000002"
    assert EvidenceLog.verify(p) is True


def test_status_states(tmp_path):
    p = tmp_path / "ev.jsonl"
    assert EvidenceLog.status(p) == "missing"
    _add(EvidenceLog(p), 1)
    assert EvidenceLog.status(p) == "verified"
    p.write_text(p.read_text(encoding="utf-8").replace("act1", "act9"), encoding="utf-8")
    assert EvidenceLog.status(p) == "broken"


def test_non_string_keys_verify(tmp_path):
    from datetime import date
    p = tmp_path / "ev.jsonl"
    log = EvidenceLog(p)
    log.append("rules", "x", [], {1: "a", 10: "b", 9: "c"}, {"o": {2: [date(2026, 1, 1)]}}, None, None)
    log.append("rules", "y", [], {date(2026, 10, 31): 1.5}, {}, None, None)
    assert EvidenceLog.verify(p) is True


def test_committed_demo_log_still_verifies():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "results" / "demo"
    logs = sorted(root.glob("*/evidence.jsonl"))
    assert logs and all(EvidenceLog.verify(x) for x in logs)
