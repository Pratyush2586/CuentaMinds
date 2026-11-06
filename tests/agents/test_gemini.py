import json

import pytest

from closeops.agents import gemini, team
from closeops.agents.gemini import GeminiHTTPError, GeminiLLM
from helpers import inv


@pytest.fixture
def tds(make_ds):
    return make_ds(invoices=[inv("I1", "V1", "INV-100", "2026-10-01", 100.0)],
                   tax_rates=[{"country": "DE", "rate": 0.19, "kind": "standard"}])


def _call(name, args, id=None, sig="s1"):
    fc = {"name": name, "args": args}
    if id:
        fc["id"] = id
    return {"functionCall": fc, "thoughtSignature": sig}


def _resp(*parts, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)},
                            "finishReason": finish}]}


class Transport:
    """Scripted POST: each item is a response dict or an exception to raise."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, url, body):
        self.requests.append((url, json.loads(json.dumps(body))))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def llm(ds, transport, **kw):
    return GeminiLLM(ds, api_key="k", post=transport, sleep=lambda s: None, **kw)


def test_loop_tool_then_verdict(tds):
    verdict = {"verdict": "confirm", "confidence": 1.4, "explanation": "x" * 900}
    t = Transport([_resp(_call("tax_rule", {"country": "DE"}, id="c1")),
                   _resp(_call("submit_verdict", verdict))])
    out = llm(tds, t, triage_model="gemini-test").resolve("Tax Reviewer", "claude-sonnet-5-5",
                                                          {"a": 1}, {})
    assert out["confidence"] == 1.0 and len(out["explanation"]) == team.MAX_EXPLANATION
    (url1, first), (_, second) = t.requests
    assert url1.endswith("/models/gemini-test:generateContent")
    names = [d["name"] for d in first["tools"][0]["functionDeclarations"]]
    assert names == [s["name"] for s in team.TOOL_SPECS] + ["submit_verdict"]
    assert "Tax Reviewer" in first["systemInstruction"]["parts"][0]["text"]
    # model turn echoed verbatim (keeps thoughtSignature), then the tool result
    assert second["contents"][1]["parts"][0]["thoughtSignature"] == "s1"
    fr = second["contents"][2]["parts"][0]["functionResponse"]
    assert fr["name"] == "tax_rule" and fr["id"] == "c1" and fr["response"]["rates"][0]["rate"] == 0.19


def test_controller_uses_controller_model_and_long_memo(tds):
    verdict = {"verdict": "confirm", "confidence": 0.9, "explanation": "m" * 3000}
    t = Transport([_resp(_call("submit_verdict", verdict))])
    out = llm(tds, t, controller_model="gemini-ctl").resolve("Controller", "claude-opus-5-5", {}, {})
    assert len(out["explanation"]) == 3000
    assert t.requests[0][0].endswith("/models/gemini-ctl:generateContent")


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_request_errors_trip_breaker(tds, code):
    t = Transport([GeminiHTTPError(code, "API key not valid")])
    with pytest.raises(team.AgentsUnavailable, match=str(code)):
        llm(tds, t).resolve("AP Clerk", "m", {}, {})


def test_rate_limit_retries_then_succeeds(tds):
    slept = []
    verdict = {"verdict": "dismiss", "confidence": 0.7, "explanation": "ok"}
    t = Transport([GeminiHTTPError(429, '{"error": {"details": [{"retryDelay": "7s"}]}}'),
                   GeminiHTTPError(503, "busy"), _resp(_call("submit_verdict", verdict))])
    g = GeminiLLM(tds, api_key="k", post=t, sleep=slept.append)
    assert g.resolve("AP Clerk", "m", {}, {})["verdict"] == "dismiss"
    assert slept[0] == 7.0 and len(slept) == 2


def test_rate_limit_gives_up_as_agent_error(tds):
    t = Transport([GeminiHTTPError(429, "quota")] * (gemini.MAX_RETRIES + 1))
    with pytest.raises(team.AgentError, match="429") as e:
        llm(tds, t).resolve("AP Clerk", "m", {}, {})
    assert not isinstance(e.value, team.AgentsUnavailable)


def test_no_verdict_and_blocked(tds):
    t = Transport([_resp({"text": "I think it is fine."})])
    with pytest.raises(team.AgentError, match="no verdict"):
        llm(tds, t).resolve("AP Clerk", "m", {}, {})
    t = Transport([{"promptFeedback": {"blockReason": "SAFETY"}}])
    with pytest.raises(team.AgentError, match="SAFETY"):
        llm(tds, t).resolve("AP Clerk", "m", {}, {})
    loop = [_resp(_call("tax_rule", {"country": "DE"})) for _ in range(team.MAX_TURNS)]
    with pytest.raises(team.AgentError, match="turns"):
        llm(tds, Transport(loop)).resolve("AP Clerk", "m", {}, {})


def test_missing_key_is_unavailable(tds, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(team.AgentsUnavailable, match="GEMINI_API_KEY"):
        GeminiLLM(tds)


def test_make_llm_picks_provider(tds, monkeypatch):
    seen = {}
    monkeypatch.setattr(gemini, "GeminiLLM", lambda ds, **kw: seen.setdefault("g", kw))
    monkeypatch.setattr(team, "ClaudeLLM", lambda ds: "claude")
    team.make_llm(tds, {"agents": {"provider": "gemini", "gemini_model": "a",
                                   "gemini_controller_model": "b"}})
    assert seen["g"] == {"triage_model": "a", "controller_model": "b"}
    assert team.make_llm(tds, {"agents": {"provider": "claude"}}) == "claude"
    assert team.make_llm(tds, {}) == "claude"
    with pytest.raises(ValueError, match="provider"):
        team.make_llm(tds, {"agents": {"provider": "nope"}})


def test_daily_quota_trips_breaker_without_retry(tds):
    slept = []
    body = '{"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}'
    t = Transport([GeminiHTTPError(429, body)])
    g = GeminiLLM(tds, api_key="k", post=t, sleep=slept.append)
    with pytest.raises(team.AgentsUnavailable, match="daily quota"):
        g.resolve("AP Clerk", "m", {}, {})
    assert slept == [] and len(t.requests) == 1
