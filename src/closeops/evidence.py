"""Tamper-evident (hash-chained) evidence log, one JSON object per line."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _digest(event_without_hash: dict) -> str:
    payload = event_without_hash["prev_hash"] + json.dumps(event_without_hash, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EvidenceLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._count = 0
        self._last_hash = GENESIS
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                self._count += 1
                try:
                    self._last_hash = json.loads(line)["hash"]
                except (ValueError, KeyError, TypeError):
                    pass  # a damaged file is reported by verify(), not here

    def append(self, actor: str, action: str, inputs: list[str], evidence: dict,
               output: dict, confidence: float | None, tier: str | None) -> dict:
        event = {
            "id": f"EV-{self._count + 1:06d}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor, "action": action, "inputs": inputs, "evidence": evidence,
            "output": output, "confidence": confidence, "tier": tier,
            "prev_hash": self._last_hash,
        }
        event["hash"] = _digest(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        self._count += 1
        self._last_hash = event["hash"]
        return event

    @staticmethod
    def verify(path: Path | str) -> bool:
        p = Path(path)
        if not p.exists():
            return True
        prev = GENESIS
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                claimed = event.pop("hash")
                if event["prev_hash"] != prev or _digest(event) != claimed:
                    return False
            except (ValueError, KeyError, TypeError, AttributeError):
                return False
            prev = claimed
        return True
