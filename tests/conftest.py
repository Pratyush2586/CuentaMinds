from pathlib import Path
from datetime import date

import pandas as pd
import pytest
import yaml

from closeops.model import CANONICAL, Dataset


@pytest.fixture
def make_ds():
    def _make(period_end="2026-10-31", **tables):
        out = {}
        for name, rows in tables.items():
            cols = CANONICAL[name] + ["source_file", "source_row"]
            df = pd.DataFrame(rows).reindex(columns=cols)
            df = df.astype(object).where(df.notna(), pd.NA)
            df["source_file"] = "test"
            df["source_row"] = list(range(len(df)))
            out[name] = df
        return Dataset(tables=out, period_end=date.fromisoformat(period_end))

    return _make


@pytest.fixture
def policy():
    path = Path(__file__).resolve().parent.parent / "config" / "policy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))
