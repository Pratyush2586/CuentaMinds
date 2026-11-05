"""Event-specific submission exporter (written on build day)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from closeops.pipeline import RunResult


def export_event(run: RunResult, out_dir: Path) -> list[Path]:
    raise NotImplementedError("write on build day once the submission format is announced")
