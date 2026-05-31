from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch, tmp_path):
    """Disable the disk cache and isolate the run DB during tests so assertions
    are deterministic and never pollute (or read) real on-disk state."""
    monkeypatch.setenv("STOCK_AGENT_CACHE", "false")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("STOCK_AGENT_DB", str(tmp_path / "runs.db"))
