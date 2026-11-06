import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from closeops.agents import team, tools
from closeops.agents.team import ClaudeLLM, controller_memo, resolve_residuals
from closeops.evidence import EvidenceLog
from closeops.model import Finding, Tier
from closeops.pipeline import run_close
from closeops.policy import assign_tiers
from closeops.synth.generate import generate
from helpers import inv


class FakeLLM:
    """Scripted LLM: verdicts keyed by finding_id (default confirm 0.8)."""

    def __init__(self, verdicts=None, raises=None):
        self.verdicts, self.raises, self.calls = verdicts or {}, raises, []
        self._lock = threading.Lock()

    def resolve(self, role, model, finding, context):
        with self._lock:
            self.calls.append((role, model, finding.get("finding_id")))
        if self.raises:
            raise self.raises
        key = finding.get("finding_id", "controller")
        return self.verdicts.get(key, {"verdict": "confirm", "confidence": 0.8,
                                       "explanation": f"fake {role}"})


def mk(check, category, conf, rid="A", severity="medium", amount=100.0):
    f = Finding(check=check, category=category, severity=severity, entity="E1",
                record_type="invoice", record_ids=[rid], evidence={}, confidence=conf,
                suggested_action="x", amount_eur=amount)
    return f


def run(ds, policy, findings, llm, tmp_path):
    assign_tiers(findings, policy)
    log = EvidenceLog(tmp_path / "ev.jsonl")
    notes = []
    out = resolve_residuals(ds, findings, policy, llm, log, notes)
    events = [json.loads(line) for line in (tmp_path / "ev.jsonl").read_text().splitlines()]
    return out, notes, events


@pytest.fixture
def ds(make_ds):
    return make_ds(invoices=[inv("I1", "V1", "N1", "2026-10-01", 100.0)])


def test_only_residuals_sent(ds, policy, tmp_path):
    fs = [mk("dup_exact", "duplicate", 0.95, "H"),      # confident: not sent
          mk("dup_near", "duplicate", 0.7, "M"),        # residual
          mk("dup_near", "duplicate", 0.4, "L"),        # below hold floor: not sent
          mk("round_amount", "fraud", 0.7, "S"),        # soft: never sent
          mk("split_invoices", "fraud", 0.95, "F"),     # fraud always sent
          mk("je_unbalanced", "gl", 0.7, "G")]          # no role
    llm = FakeLLM()
    resolve = run(ds, policy, fs, llm, tmp_path)
    assert sorted(c[2] for c in llm.calls) == ["dup_near:M", "split_invoices:F"]
    assert {c[0]: c[1] for c in llm.calls} == {"AP Clerk": "claude-sonnet-5-5",
                                               "Fraud Analyst": "claude-sonnet-5-5"}
    assert len(resolve[0]) == 6


def test_roles_by_category(ds, policy, tmp_path):
    fs = [mk("c1", "match", 0.7, "1"), mk("c2", "bankrec", 0.7, "2"), mk("c3", "tax", 0.7, "3")]
    llm = FakeLLM()
    run(ds, policy, fs, llm, tmp_path)
    assert {c[0] for c in llm.calls} == {"AP Clerk", "Reconciler", "Tax Reviewer"}


def test_dismiss_removes_and_logs(ds, policy, tmp_path):
    f = mk("dup_near", "duplicate", 0.7)
    llm = FakeLLM({f.finding_id: {"verdict": "dismiss", "confidence": 0.9, "explanation": "fp"}})
    out, _, events = run(ds, policy, [f], llm, tmp_path)
    assert out == []
    assert len(events) == 1 and events[0]["action"] == "agent_review"
    assert events[0]["output"]["applied"] == "dismiss"


def test_confirm_raises_confidence_and_sets_fields(ds, policy, tmp_path):
    f = mk("dup_near", "duplicate", 0.7)
    llm = FakeLLM({f.finding_id: {"verdict": "confirm", "confidence": 0.95, "explanation": "real"}})
    out, _, _ = run(ds, policy, [f], llm, tmp_path)
    assert out[0].confidence == 0.95 and out[0].resolved_by == "AP Clerk"
    assert out[0].explanation == "real" and out[0].tier is Tier.AUTO


def test_confirm_never_lowers_confidence(ds, policy, tmp_path):
    f = mk("dup_near", "duplicate", 0.8)
    llm = FakeLLM({f.finding_id: {"verdict": "confirm", "confidence": 0.65, "explanation": "meh"}})
    out, _, _ = run(ds, policy, [f], llm, tmp_path)
    assert out[0].confidence == 0.8


def test_escalate_forces_hold_surviving_retier(ds, policy, tmp_path):
    f = mk("dup_near", "duplicate", 0.7)
    llm = FakeLLM({f.finding_id: {"verdict": "escalate", "confidence": 0.99, "explanation": "look"}})
    out, _, _ = run(ds, policy, [f], llm, tmp_path)
    assert out[0].confidence == 0.7  # escalate does not raise confidence
    assert out[0].tier is Tier.HOLD and out[0].requires_human_review


def test_iban_confirm_still_hold(ds, policy, tmp_path):
    f = mk("iban_changed_before_payment", "fraud", 0.7, severity="medium")
    llm = FakeLLM({f.finding_id: {"verdict": "confirm", "confidence": 0.99, "explanation": "ok"}})
    out, _, _ = run(ds, policy, [f], llm, tmp_path)
    assert out[0].tier is Tier.HOLD


@pytest.mark.parametrize("check", ["iban_not_in_master", "shared_iban"])
def test_iban_dismiss_becomes_hold(ds, policy, tmp_path, check):
    f = mk(check, "fraud", 0.7)
    llm = FakeLLM({f.finding_id: {"verdict": "dismiss", "confidence": 0.99, "explanation": "fp"}})
    out, notes, events = run(ds, policy, [f], llm, tmp_path)
    assert len(out) == 1 and out[0].tier is Tier.HOLD
    assert out[0].confidence == 0.7  # agent's 0.99 'dismiss' confidence is not applied
    assert events[0]["output"]["confidence"] == 0.99
    assert any("overridden" in n for n in notes)
    assert events[0]["output"]["applied"] == "escalate"


def test_agent_error_leaves_finding_unchanged(ds, policy, tmp_path):
    f = mk("dup_near", "duplicate", 0.7)
    out, notes, events = run(ds, policy, [f], FakeLLM(raises=RuntimeError("boom")), tmp_path)
    assert len(out) == 1 and out[0].confidence == 0.7 and out[0].resolved_by == "rule"
    assert out[0].tier is Tier.AUTO_NOTE
    assert any("agent error" in n and "boom" in n for n in notes)
    assert events[0]["action"] == "agent_error" and events[0]["actor"] == "AP Clerk"


def test_malformed_verdict_is_an_error(ds, policy, tmp_path):
    f = mk("dup_near", "duplicate", 0.7)
    llm = FakeLLM({f.finding_id: {"verdict": "maybe", "confidence": 0.5, "explanation": ""}})
    out, notes, _ = run(ds, policy, [f], llm, tmp_path)
    assert out[0].resolved_by == "rule" and notes


def test_one_event_per_call_actor_is_role(ds, policy, tmp_path):
    fs = [mk("dup_near", "duplicate", 0.7, "1"), mk("c", "bankrec", 0.7, "2"),
          mk("x", "tax", 0.7, "3"), mk("split_invoices", "fraud", 0.95, "4")]
    _, _, events = run(ds, policy, fs, FakeLLM(), tmp_path)
    assert sorted(e["actor"] for e in events) == ["AP Clerk", "Fraud Analyst", "Reconciler",
                                                  "Tax Reviewer"]
    assert all(e["action"] == "agent_review" and len(e["inputs"]) == 1 for e in events)
    assert EvidenceLog.verify(tmp_path / "ev.jsonl")


def test_deterministic_order(ds, policy, tmp_path):
    fs = [mk("dup_near", "duplicate", 0.7, str(i)) for i in range(20)]
    _, _, events = run(ds, policy, fs, FakeLLM(), tmp_path)
    ids = [e["inputs"][0] for e in events]
    assert ids == sorted(ids)


def test_controller_memo(ds):
    fs = [mk("dup_near", "duplicate", 0.7)]
    llm = FakeLLM({"controller": {"verdict": "confirm", "confidence": 0.9, "explanation": "# Memo"}})
    memo, ready = controller_memo(ds, fs, None, llm)
    assert (memo, ready) == ("# Memo", True)
    assert llm.calls[0][:2] == ("Controller", "claude-opus-5-5")
    llm = FakeLLM({"controller": {"verdict": "escalate", "confidence": 0.9, "explanation": "no"}})
    assert controller_memo(ds, fs, None, llm) == ("no", False)


def test_controller_error(ds):
    memo, ready = controller_memo(ds, [], None, FakeLLM(raises=RuntimeError("down")))
    assert memo == "Controller unavailable: down; close not signed off." and ready is False


# ---- tools -----------------------------------------------------------------

@pytest.fixture
def tds(make_ds):
    return make_ds(
        invoices=[inv("I1", "V1", "INV-100", "2026-10-01", 100.0),
                  inv("I2", "V1", "INV-100", "2026-10-02", 100.0),
                  inv("I3", "V2", "ZZZ", "2026-10-02", 5.0)],
        vendors=[{"vendor_id": "V1", "entity": "E1", "name": "Acme", "country": "DE",
                  "iban": "DE00"}],
        vendor_bank_history=[{"vendor_id": "V1", "iban": "DE00", "changed_at": "2026-09-01",
                              "changed_by": "x"}],
        tax_rates=[{"country": "DE", "rate": 0.19, "kind": "standard"}])


def test_tools(tds):
    recs = tools.get_records(tds, "invoice", ["I1"])
    assert recs[0]["invoice_id"] == "I1" and isinstance(recs[0]["invoice_date"], str)
    json.dumps(recs)
    assert "error" in tools.get_records(tds, "nope", ["x"])[0]
    prof = tools.vendor_profile(tds, "V1")
    assert prof["vendor"]["name"] == "Acme" and prof["invoice_stats"]["count"] == 2
    assert len(prof["bank_history"]) == 1
    assert [r["invoice_id"] for r in tools.similar_invoices(tds, "I1")] == ["I2"]
    assert tools.tax_rule(tds, "de")["rates"][0]["rate"] == 0.19


def test_tool_rows_capped(make_ds):
    ds = make_ds(invoices=[inv(f"I{i}", "V1", "N", "2026-10-01", 1.0) for i in range(40)])
    assert len(tools.get_records(ds, "invoice", [f"I{i}" for i in range(40)])) == tools.MAX_ROWS


def test_dispatch(tds):
    assert dispatch_ok(tds)
    assert "error" in tools.dispatch(tds, "nope", {})
    assert "error" in tools.dispatch(tds, "tax_rule", {"bad": 1})
    assert all(s["strict"] and s["input_schema"]["additionalProperties"] is False
               for s in tools.TOOL_SPECS)


def dispatch_ok(ds):
    out = tools.dispatch(ds, "similar_invoices", {"invoice_id": "I1"})
    return out["result"][0]["invoice_id"] == "I2"


# ---- ClaudeLLM with a scripted client (no network) -------------------------

def _resp(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def _use(name, inp, id="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id)


class ScriptedClient:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def test_claude_llm_loop(tds):
    verdict = {"verdict": "confirm", "confidence": 1.7, "explanation": "x" * 900}
    client = ScriptedClient([_resp("tool_use", _use("tax_rule", {"country": "DE"})),
                             _resp("tool_use", _use("submit_verdict", verdict))])
    out = ClaudeLLM(tds, client=client).resolve("Tax Reviewer", "claude-sonnet-5-5", {"a": 1}, {})
    assert out["confidence"] == 1.0 and len(out["explanation"]) == 600
    first, second = client.requests
    for req in client.requests:
        assert req["model"] == "claude-sonnet-5-5"
        assert req["betas"] == ["server-side-fallback-2026-07-01"]
        assert req["fallbacks"] == "default"
        assert req["tool_choice"] == {"type": "auto"}
        assert all(t["strict"] is True for t in req["tools"])
        assert {"thinking", "temperature"}.isdisjoint(req)
    assert first["output_config"] == {"effort": "low"}
    result = second["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "0.19" in result["content"]


def test_controller_request_and_long_memo(tds):
    verdict = {"verdict": "confirm", "confidence": 0.9, "explanation": "m" * 3000}
    client = ScriptedClient([_resp("tool_use", _use("submit_verdict", verdict))])
    out = ClaudeLLM(tds, client=client).resolve("Controller", "claude-opus-5-5", {}, {})
    assert len(out["explanation"]) == 3000
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "medium"}
    assert all(t["strict"] is True for t in req["tools"])
    memo, ready = controller_memo(tds, [], None, FakeLLM(
        {"controller": {"verdict": "confirm", "confidence": 1, "explanation": "m" * 3000}}))
    assert len(memo) == 3000 and ready


def test_claude_llm_failures(tds):
    llm = ClaudeLLM(tds, client=ScriptedClient([_resp("refusal")]))
    with pytest.raises(team.AgentError, match="refusal"):
        llm.resolve("AP Clerk", "claude-sonnet-5-5", {}, {})
    loop = [_resp("tool_use", _use("tax_rule", {"country": "DE"})) for _ in range(team.MAX_TURNS)]
    with pytest.raises(team.AgentError, match="turns"):
        ClaudeLLM(tds, client=ScriptedClient(loop)).resolve("AP Clerk", "m", {}, {})
    import anthropic

    class Boom:
        beta = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: (_ for _ in ()).throw(
                anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="http://x")))))
    with pytest.raises(team.AgentError, match="APIConnectionError"):
        ClaudeLLM(tds, client=Boom()).resolve("AP Clerk", "m", {}, {})


# ---- pipeline ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent.parent


def _paths():
    return ROOT / "config" / "mappings.synth.yaml", ROOT / "config" / "policy.yaml"


def _fingerprint(res):
    return [(f.finding_id, f.tier, f.confidence, f.resolved_by) for f in res.findings]


def test_run_close_agents_unavailable_is_rules_only(tmp_path, monkeypatch):
    M, P = _paths()
    raw = generate(tmp_path / "raw", seed=7, n_invoices=200)
    base = run_close(raw, M, P, tmp_path / "base")

    def no_client(ds, client=None):
        raise RuntimeError("no credentials")
    monkeypatch.setattr(team, "ClaudeLLM", no_client)
    res = run_close(raw, M, P, tmp_path / "r", use_agents=True)
    assert _fingerprint(res) == _fingerprint(base)
    assert "agents unavailable: no credentials" in res.notes
    assert not (res.run_dir / "close_memo.md").exists()


def test_run_close_failing_client_keeps_rules_result(tmp_path, monkeypatch):
    M, P = _paths()
    raw = generate(tmp_path / "raw", seed=7, n_invoices=200)
    base = run_close(raw, M, P, tmp_path / "base")
    monkeypatch.setattr(team, "ClaudeLLM", lambda ds, client=None: FakeLLM(raises=RuntimeError("x")))
    res = run_close(raw, M, P, tmp_path / "r", use_agents=True)
    assert _fingerprint(res) == _fingerprint(base)
    assert any(n.startswith("agent error") for n in res.notes)
    memo = (res.run_dir / "close_memo.md").read_text(encoding="utf-8")
    assert memo.startswith("Controller unavailable") and not res.close_ready
    assert EvidenceLog.verify(res.run_dir / "evidence.jsonl")


def test_run_close_with_fake_agents(tmp_path, monkeypatch):
    M, P = _paths()
    raw = generate(tmp_path / "raw", seed=7, n_invoices=200)
    fake = FakeLLM({"controller": {"verdict": "confirm", "confidence": 0.9,
                                   "explanation": "All clear."}})
    monkeypatch.setattr(team, "ClaudeLLM", lambda ds, client=None: fake)
    res = run_close(raw, M, P, tmp_path / "r", use_agents=True)
    assert res.close_ready is True
    assert (res.run_dir / "close_memo.md").read_text(encoding="utf-8").strip() == "All clear."
    assert any(c[0] == "Controller" for c in fake.calls)
    assert all(f.tier is not None for f in res.findings)


def test_run_close_no_credentials_single_note(tmp_path, monkeypatch):
    M, P = _paths()
    raw = generate(tmp_path / "raw", seed=7, n_invoices=200)
    base = run_close(raw, M, P, tmp_path / "base")
    msg = "Could not resolve authentication method. Expected one of api_key"
    calls = []

    def boom(**kw):
        calls.append(kw)
        raise TypeError(msg)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom)))
    monkeypatch.setattr(team, "ClaudeLLM", lambda ds: ClaudeLLM(ds, client=client))
    res = run_close(raw, M, P, tmp_path / "r", use_agents=True)
    unavailable = [n for n in res.notes if n.startswith("agents unavailable")]
    assert len(unavailable) == 1 and "authentication" in unavailable[0]
    assert not any(n.startswith("agent error") for n in res.notes)
    assert _fingerprint(res) == _fingerprint(base)
    assert not (res.run_dir / "close_memo.md").exists() and res.close_ready is None
    assert len(calls) <= team.MAX_WORKERS  # breaker stops further dispatch


# ---- budgets and request-shape errors --------------------------------------

def _status_error(cls, code):
    import httpx2 as httpx
    req = httpx.Request("POST", "http://x")
    return cls("nope", response=httpx.Response(code, request=req), body=None)


@pytest.mark.parametrize("name,code", [("BadRequestError", 400), ("NotFoundError", 404)])
def test_bad_request_and_not_found_trip_breaker(tds, policy, tmp_path, name, code):
    import anthropic
    exc = _status_error(getattr(anthropic, name), code)
    calls = []

    def boom(**kw):
        calls.append(kw)
        raise exc
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom)))
    with pytest.raises(team.AgentsUnavailable, match=name):
        ClaudeLLM(tds, client=client).resolve("AP Clerk", "m", {}, {})
    fs = [mk("dup_near", "duplicate", 0.7, f"R{i}") for i in range(20)]
    before = [(f.finding_id, f.confidence) for f in fs]
    assign_tiers(fs, policy)
    notes = []
    out = resolve_residuals(tds, fs, policy, ClaudeLLM(tds, client=client),
                            EvidenceLog(tmp_path / "ev.jsonl"), notes)
    unavailable = [n for n in notes if n.startswith("agents unavailable")]
    assert len(unavailable) == 1 and name in unavailable[0]
    assert not any(n.startswith("agent error") for n in notes)
    assert [(f.finding_id, f.confidence) for f in out] == before


def test_max_residuals_picks_largest(ds, policy, tmp_path):
    policy = {**policy, "agents": {"max_residuals": 2, "wall_clock_seconds": 300}}
    fs = [mk("dup_near", "duplicate", 0.7, "A", amount=10.0),
          mk("dup_near", "duplicate", 0.7, "B", amount=900.0),
          mk("dup_near", "duplicate", 0.7, "C", amount=50.0),
          mk("dup_near", "duplicate", 0.7, "D", amount=50.0)]
    llm = FakeLLM()
    out, notes, _ = run(ds, policy, fs, llm, tmp_path)
    assert sorted(c[2] for c in llm.calls) == ["dup_near:B", "dup_near:C"]  # tie C/D -> C
    assert [n for n in notes if "max_residuals" in n] == [
        "agents: 2 of 4 residuals reviewed (max_residuals=2); 2 left to the rules"]
    assert len(out) == 4


def test_wall_clock_budget_stops_dispatch(ds, policy, tmp_path, monkeypatch):
    policy = {**policy, "agents": {"max_residuals": 40, "wall_clock_seconds": 300}}
    now = [0.0]
    monkeypatch.setattr(team, "_clock", lambda: now[0])
    monkeypatch.setattr(team, "MAX_WORKERS", 1)

    class SlowLLM(FakeLLM):
        def resolve(self, role, model, finding, context):
            now[0] += 100.0
            return super().resolve(role, model, finding, context)
    fs = [mk("dup_near", "duplicate", 0.7, f"R{i}", amount=100.0 - i) for i in range(6)]
    llm = SlowLLM()
    out, notes, events = run(ds, policy, fs, llm, tmp_path)
    assert len(llm.calls) == 3
    assert [n for n in notes if "wall_clock" in n] == [
        "agents: wall_clock_seconds=300 reached; 3 residuals left to the rules"]
    assert not any(n.startswith("agent error") for n in notes)
    assert sum(f.resolved_by == "AP Clerk" for f in out) == 3
    assert len(events) == 3


def test_breaker_note_replaces_budget_note(ds, policy, tmp_path):
    policy = {**policy, "agents": {"max_residuals": 1, "wall_clock_seconds": 300}}
    fs = [mk("dup_near", "duplicate", 0.7, r) for r in "AB"]
    assign_tiers(fs, policy)
    notes = []
    resolve_residuals(ds, fs, policy, FakeLLM(raises=team.AgentsUnavailable("no key")),
                      EvidenceLog(tmp_path / "ev.jsonl"), notes)
    assert notes == ["agents unavailable: no key"]


def test_max_workers_from_policy(ds, policy, tmp_path, monkeypatch):
    seen = {}
    real = team.ThreadPoolExecutor

    def pool(max_workers):
        seen["n"] = max_workers
        return real(max_workers=max_workers)
    monkeypatch.setattr(team, "ThreadPoolExecutor", pool)
    policy = {**policy, "agents": {"max_residuals": 40, "wall_clock_seconds": 300, "max_workers": 2}}
    run(ds, policy, [mk("dup_near", "duplicate", 0.7)], FakeLLM(), tmp_path)
    assert seen["n"] == 2
