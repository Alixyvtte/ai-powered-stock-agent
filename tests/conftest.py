from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch, tmp_path):
    """Disable the disk cache during tests so call-count assertions are
    deterministic and never polluted by a real on-disk cache."""
    monkeypatch.setenv("STOCK_AGENT_CACHE", "false")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path / "cache"))
