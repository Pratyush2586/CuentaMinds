"""Gemini implementation of the agent LLM (free tier): same prompts, tools and verdict
contract as ClaudeLLM, over the generateContent REST API with a manual function-call loop.

Standard library only; the key travels in the x-goog-api-key header, never in a URL.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from closeops.agents.team import (_CONTROLLER_SYSTEM, _SYSTEM, MAX_EXPLANATION, MAX_MEMO,
                                  MAX_TURNS, SUBMIT_VERDICT, AgentError, AgentsUnavailable,
                                  _parse_verdict)
from closeops.agents.tools import TOOL_SPECS, dispatch
from closeops.model import Dataset

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_RETRIES = 3          # per request, on 429 / 5xx
MAX_BACKOFF_SECONDS = 30.0
_FATAL = {400, 401, 403, 404}  # key, model name or request shape: every call fails the same way


class GeminiHTTPError(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(f"HTTP {code}: {text[:300]}")
        self.code, self.text = code, text


def _declarations() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "parametersJsonSchema": t["input_schema"]} for t in TOOL_SPECS + [SUBMIT_VERDICT]]


def _retry_delay(err: GeminiHTTPError, attempt: int) -> float:
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', err.text)
    return min(MAX_BACKOFF_SECONDS, float(m.group(1)) if m else 2.0 * 2 ** attempt)


class GeminiLLM:
    def __init__(self, ds: Dataset, api_key: str | None = None, triage_model: str = DEFAULT_MODEL,
                 controller_model: str = DEFAULT_MODEL, post=None, sleep=time.sleep):
        self.ds = ds
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise AgentsUnavailable("GEMINI_API_KEY not set")
        self.triage_model, self.controller_model = triage_model, controller_model
        self.post = post or self._post
        self.sleep = sleep

    def _post(self, url: str, body: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise GeminiHTTPError(exc.code, exc.read().decode("utf-8", "replace")) from None

    def _generate(self, model: str, body: dict) -> dict:
        url = API.format(model=model)
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self.post(url, body)
            except GeminiHTTPError as exc:
                if exc.code in _FATAL:
                    raise AgentsUnavailable(f"Gemini {exc}") from None
                if exc.code == 429 and "PerDay" in exc.text:  # resets tomorrow: retrying only burns time
                    raise AgentsUnavailable(f"Gemini daily quota exhausted for {model}") from None
                if attempt == MAX_RETRIES or not (exc.code == 429 or exc.code >= 500):
                    raise AgentError(f"Gemini HTTP {exc.code}") from None
                self.sleep(_retry_delay(exc, attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise AgentError(f"Gemini connection: {type(exc).__name__}") from None
        raise AgentError("unreachable")

    def resolve(self, role: str, model: str, finding: dict, context: dict) -> dict:
        # `model` names a Claude model; Gemini picks its own by role
        controller = role == "Controller"
        gmodel = self.controller_model if controller else self.triage_model
        system = _CONTROLLER_SYSTEM if controller else _SYSTEM.format(role=role)
        prompt = json.dumps({"item": finding, "context": context}, default=str, sort_keys=True)
        contents: list[dict] = [{"role": "user", "parts": [{"text": prompt}]}]
        for _ in range(MAX_TURNS):
            data = self._generate(gmodel, {
                "systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                "tools": [{"functionDeclarations": _declarations()}],
                "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                "generationConfig": {"maxOutputTokens": 8192}})
            cands = data.get("candidates") or []
            if not cands:
                reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
                raise AgentError(f"Gemini blocked: {reason}")
            content = cands[0].get("content") or {}
            calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
            for c in calls:
                if c.get("name") == "submit_verdict":
                    return _parse_verdict(c.get("args") or {}, MAX_MEMO if controller else MAX_EXPLANATION)
            if not calls:
                raise AgentError(f"no verdict submitted ({cands[0].get('finishReason')})")
            contents.append({"role": "model", "parts": content["parts"]})  # verbatim: keeps thoughtSignature
            results = []
            for c in calls:
                fr = {"name": c["name"], "response": dispatch(self.ds, c["name"], c.get("args") or {})}
                if c.get("id"):
                    fr["id"] = c["id"]
                results.append({"functionResponse": fr})
            contents.append({"role": "user", "parts": results})
        raise AgentError(f"no verdict within {MAX_TURNS} turns")
