from __future__ import annotations

import os
import time

from stock_agent import cache


def test_cache_disabled_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_AGENT_CACHE", "false")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path))
    cache.set("ns", "k", {"a": 1})
    assert cache.get("ns", "k", 100) is None


def test_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_AGENT_CACHE", "true")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path))
    cache.set("ns", "key", {"a": 1, "b": [2, 3]})
    assert cache.get("ns", "key", 100) == {"a": 1, "b": [2, 3]}


def test_cache_ttl_expiry(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_AGENT_CACHE", "true")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path))
    cache.set("ns", "k", 123)
    stale = time.time() - 1000
    os.utime(cache._path("ns", "k"), (stale, stale))
    assert cache.get("ns", "k", 100) is None      # 1000s old > 100s ttl
    assert cache.get("ns", "k", None) == 123       # no ttl -> always valid


def test_cached_helper_calls_producer_once(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_AGENT_CACHE", "true")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path))
    calls = []

    def produce():
        calls.append(1)
        return "value"

    v1, hit1 = cache.cached("ns", "key", 100, produce)
    v2, hit2 = cache.cached("ns", "key", 100, produce)

    assert v1 == v2 == "value"
    assert hit1 is False and hit2 is True
    assert len(calls) == 1


def test_web_search_uses_cache(monkeypatch, tmp_path):
    """Second call for the same query is served from cache (provider not re-hit)."""
    monkeypatch.setenv("STOCK_AGENT_CACHE", "true")
    monkeypatch.setenv("STOCK_AGENT_CACHE_DIR", str(tmp_path))

    from stock_agent.tools import web_search as ws

    calls = []

    def fake_uncached(query, max_results=5, timeout_s=25):
        calls.append(query)
        return [ws.WebDocument(title="T", url="https://e.com/1", content="body")]

    monkeypatch.setattr(ws, "_web_search_uncached", fake_uncached)

    first = ws.web_search("nvda outlook", max_results=3)
    second = ws.web_search("nvda outlook", max_results=3)

    assert len(first) == 1 and first[0].url == "https://e.com/1"
    assert [d.url for d in second] == [d.url for d in first]
    assert calls == ["nvda outlook"], "provider should be hit once; second call cached"
