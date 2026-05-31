"""Tiny dependency-free disk cache with TTL.

Used to memoize slow, idempotent I/O (web search, page content, market
snapshots) so repeated/demo queries return near-instantly and API quota is
saved. Any error degrades to a cache miss — caching must never break the
pipeline.

Gating is environment-driven so it is uniform across the CLI, the web app and
the tools layer:
  STOCK_AGENT_CACHE       on/off  (default on)
  STOCK_AGENT_CACHE_DIR   directory (default <cwd>/.cache/stock_agent)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

# Sensible default TTLs (seconds). Callers may override per call.
TTL_SEARCH = 6 * 60 * 60      # web search results
TTL_CONTENT = 6 * 60 * 60     # fetched page content
TTL_MARKET = 10 * 60          # market snapshots (prices move)


def enabled() -> bool:
    raw = os.getenv("STOCK_AGENT_CACHE")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _root() -> Path:
    return Path(os.getenv("STOCK_AGENT_CACHE_DIR") or (Path.cwd() / ".cache" / "stock_agent"))


def _path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _root() / namespace / f"{digest}.json"


def get(namespace: str, key: str, ttl_s: Optional[int]) -> Optional[Any]:
    if not enabled():
        return None
    path = _path(namespace, key)
    try:
        if not path.exists():
            return None
        if ttl_s is not None and (time.time() - path.stat().st_mtime) > ttl_s:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)["v"]
    except Exception:
        return None


def set(namespace: str, key: str, value: Any) -> None:  # noqa: A001 - mirrors get()
    if not enabled():
        return
    path = _path(namespace, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in the same dir, then replace.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"v": value}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def cached(namespace: str, key: str, ttl_s: Optional[int], producer: Callable[[], Any]) -> Tuple[Any, bool]:
    """Return (value, was_hit). On miss, call producer() and store its result."""
    hit = get(namespace, key, ttl_s)
    if hit is not None:
        return hit, True
    value = producer()
    set(namespace, key, value)
    return value, False


__all__ = ["enabled", "get", "set", "cached", "TTL_SEARCH", "TTL_CONTENT", "TTL_MARKET"]
