"""Fetch and extract the readable main text of a web page.

Search providers only return short snippets (~150-300 chars). Feeding the
extractor the real article body dramatically improves evidence quality. This
uses requests + BeautifulSoup (already available, no heavy dependency) and
caches results on disk.

Network fetches cannot be exercised in the sandboxed dev environment; the node
that calls this is unit-tested with the fetcher mocked, and ``_extract_main_text``
is tested directly against sample HTML offline.
"""
from __future__ import annotations

import logging
from typing import List

import requests

from .. import cache

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_DROP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "noscript", "iframe", "svg", "button")


def _extract_main_text(html: str, *, max_chars: int = 5000) -> str:
    """Pull the main textual content out of an HTML document."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(list(_DROP_TAGS)):
            tag.decompose()

        root = soup.find("article") or soup.find("main") or soup.body or soup
        blocks: List[str] = []
        for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
            text = el.get_text(" ", strip=True)
            # Skip nav-ish fragments; keep substantive sentences.
            if text and len(text) > 25:
                blocks.append(text)

        joined = "\n".join(blocks).strip()
        if not joined:
            joined = " ".join(root.get_text(" ", strip=True).split())
        return joined[:max_chars]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("content extraction failed: %s", exc)
        return ""


def _fetch_uncached(url: str, *, timeout_s: int, max_chars: int) -> str:
    try:
        resp = _SESSION.get(url, headers=_HEADERS, timeout=timeout_s)
    except Exception as exc:
        logger.warning("content fetch failed for %s: %s", url, exc)
        return ""
    if resp.status_code != 200:
        return ""
    ctype = resp.headers.get("Content-Type", "").lower()
    if ctype and ("html" not in ctype and "text" not in ctype):
        return ""
    return _extract_main_text(resp.text, max_chars=max_chars)


def fetch_readable_text(url: str, *, timeout_s: int = 10, max_chars: int = 5000) -> str:
    """Return the readable main text for a URL ("" on any failure). Cached on disk."""
    if not url:
        return ""
    hit = cache.get("content", url, cache.TTL_CONTENT)
    if hit is not None:
        return hit
    text = _fetch_uncached(url, timeout_s=timeout_s, max_chars=max_chars)
    if text:
        cache.set("content", url, text)
    return text


__all__ = ["fetch_readable_text"]
