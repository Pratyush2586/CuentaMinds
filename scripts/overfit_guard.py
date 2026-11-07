"""Static overfitting guard for engine changes (deterministic, no LLM).

    python scripts/overfit_guard.py                # diff of main..HEAD in src/bankrec
    python scripts/overfit_guard.py BASE [HEAD]    # any range, e.g. 42fc572 HEAD

Flags ADDED lines in src/bankrec that look like memorisation instead of generic rules:
  - record ids from the data (ARI-/API-/ARC-/APC-/BT-YYYY-..., customer/supplier ids like CES01000123)
  - concrete dates (YYYY-MM-DD) and money-looking literals (1234.56) outside named constants
  - entity / account codes (ES01, IT01-VALD-EUR-2) used as logic
Exit 1 when anything is found, so it can gate a promotion. A flagged line can be legitimate (e.g. a POLICIES.md
threshold): then move it into a named, commented constant at the top of engine.py.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [
    ("record id", re.compile(r"\b(?:AR[IC]|AP[IC])-20\d\d-\d{5,}|\bBT-20\d\d-\d{5,}")),
    ("party id", re.compile(r"\b[CS][A-Z]{2}\d{2}\d{6,}\b")),
    ("date literal", re.compile(r"[\"']20\d\d-\d\d-\d\d[\"']")),
    ("money literal", re.compile(r"(?<![\w.])\d{3,}\.\d{2}(?![\d])")),
    ("account id", re.compile(r"\b[A-Z]{2}\d{2}-[A-Z]{3,5}-[A-Z]{3}(?:-\d)?\b")),
    ("entity code", re.compile(r"[\"'](?:AU|BR|CH|CN|DE|ES|FR|IT|MA|MX|NL|PL|PT|UK|US|ZA)\d{2}[\"']")),
]
ALLOW = re.compile(r"^\s*#|\"\"\"|^\s*[A-Z_]+\s*=")      # comments, docstrings, named constants


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    base = sys.argv[1] if len(sys.argv) > 1 else "main"
    head = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    diff = subprocess.run(["git", "diff", "-U0", f"{base}..{head}", "--", "src/bankrec"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    hits, fname, line_no = [], "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            fname = raw[6:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line_no = int(m.group(1)) - 1 if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            line_no += 1
            code = raw[1:].split("  # ")[0]   # inline comments may mention examples
            if fname.endswith("official_score.py") or ALLOW.search(code):
                continue
            for name, rx in PATTERNS:
                if rx.search(code):
                    hits.append((fname, line_no, name, code.strip()[:110]))
    print(f"overfit guard: {base}..{head}  ({len(hits)} finding(s))")
    for f, n, name, code in hits:
        print(f"  {f}:{n}  [{name}]  {code}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
