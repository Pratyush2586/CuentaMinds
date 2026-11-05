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
