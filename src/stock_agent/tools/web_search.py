from __future__ import annotations

from dataclasses import dataclass
import os
from typing import List, Optional


@dataclass(frozen=True)
class WebDocument:
    title: str
    url: str
    content: str


def _search_tavily(query: str, max_results: int, timeout_s: int) -> List[WebDocument]:
    from tavily import TavilyClient

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []

    client = TavilyClient(api_key=api_key)
    resp = client.search(
        query=query,
        max_results=max_results,
        include_raw_content=True,
    )
    results = resp.get("results", []) if isinstance(resp, dict) else []
    docs: List[WebDocument] = []
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        content = (r.get("raw_content") or r.get("content") or "").strip()
        if not (title or url or content):
            continue
        docs.append(WebDocument(title=title or url, url=url, content=content))
    return docs


def _search_duckduckgo(query: str, max_results: int) -> List[WebDocument]:
    from duckduckgo_search import DDGS

    docs: List[WebDocument] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            title = (r.get("title") or "").strip()
            url = (r.get("href") or "").strip()
            body = (r.get("body") or "").strip()
            if not (title or url or body):
                continue
            docs.append(WebDocument(title=title or url, url=url, content=body))
    return docs


def web_search(query: str, max_results: int = 5, timeout_s: int = 25) -> List[WebDocument]:
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        docs = _search_tavily(query=query, max_results=max_results, timeout_s=timeout_s)
        if docs:
            return docs
    return _search_duckduckgo(query=query, max_results=max_results)


def pick_best_docs(
    docs: List[WebDocument], *, limit: int = 6, require_url: bool = True
) -> List[WebDocument]:
    seen: set[str] = set()
    picked: List[WebDocument] = []
    for d in docs:
        key = (d.url or d.title).strip()
        if not key:
            continue
        if require_url and not d.url:
            continue
        if key in seen:
            continue
        seen.add(key)
        picked.append(d)
        if len(picked) >= limit:
            break
    return picked

