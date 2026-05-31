from __future__ import annotations

from dataclasses import dataclass
import os
import requests
import concurrent.futures
import logging
from typing import List, Optional, Dict, Any

from .. import cache

logger = logging.getLogger(__name__)

# P6: a shared Session reuses TCP/TLS connections across the many calls a single
# search pass makes to the same provider host (serper.dev / serpapi.com).
_SESSION = requests.Session()

@dataclass(frozen=True)
class WebDocument:
    title: str
    url: str
    content: str


def _search_tavily(query: str, max_results: int, timeout_s: int) -> List[WebDocument]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []

    try:
        from tavily import TavilyClient
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
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        return []





def _search_serper(query: str, max_results: int, timeout_s: int) -> List[WebDocument]:
    serper_key = os.getenv("SERPER_API_KEY")
    if not serper_key:
        return []
    
    headers = {
        "X-API-KEY": serper_key,
        "Content-Type": "application/json"
    }
    try:
        resp = _SESSION.post("https://google.serper.dev/search", headers=headers, json={"q": query, "num": max_results}, timeout=timeout_s)
        if resp.status_code == 200:
            results = resp.json().get("organic", [])
            docs: List[WebDocument] = []
            for r in results[:max_results]:
                title = (r.get("title") or "").strip()
                url = (r.get("link") or "").strip()
                content = (r.get("snippet") or "").strip()
                if title or url or content:
                    docs.append(WebDocument(title=title or url, url=url, content=content))
            return docs
    except Exception as e:
        logger.warning(f"Serper search failed: {e}")
    return []


def _search_serpapi(query: str, max_results: int, timeout_s: int) -> List[WebDocument]:
    serpapi_key = os.getenv("SERPAPI_KEY")
    if not serpapi_key:
        return []
    
    try:
        resp = _SESSION.get("https://serpapi.com/search.json", params={"q": query, "api_key": serpapi_key, "num": max_results}, timeout=timeout_s)
        if resp.status_code == 200:
            results = resp.json().get("organic_results", [])
            docs: List[WebDocument] = []
            for r in results[:max_results]:
                title = (r.get("title") or "").strip()
                url = (r.get("link") or "").strip()
                content = (r.get("snippet") or "").strip()
                if title or url or content:
                    docs.append(WebDocument(title=title or url, url=url, content=content))
            return docs
    except Exception as e:
        logger.warning(f"SerpApi search failed: {e}")
    return []





def _search_duckduckgo(query: str, max_results: int) -> List[WebDocument]:
    docs: List[WebDocument] = []
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = (r.get("title") or "").strip()
                url = (r.get("href") or "").strip()
                body = (r.get("body") or "").strip()
                if not (title or url or body):
                    continue
                docs.append(WebDocument(title=title or url, url=url, content=body))
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
    return docs


def web_search(query: str, max_results: int = 5, timeout_s: int = 25) -> List[WebDocument]:
    """Cached web search across providers.

    Results are memoized to disk (TTL ~6h); only non-empty result sets are
    cached so a transient provider failure never poisons the cache. The actual
    retrieval lives in ``_web_search_uncached``.
    """
    key = f"{query}||{max_results}"
    hit = cache.get("search", key, cache.TTL_SEARCH)
    if hit:  # non-empty cached list
        return [
            WebDocument(
                title=d.get("title", ""),
                url=d.get("url", ""),
                content=d.get("content", ""),
            )
            for d in hit
        ]

    docs = _web_search_uncached(query, max_results, timeout_s)
    if docs:
        cache.set("search", key, [{"title": d.title, "url": d.url, "content": d.content} for d in docs])
    return docs


def _web_search_uncached(query: str, max_results: int = 5, timeout_s: int = 25) -> List[WebDocument]:
    """
    Executes a web search across multiple available providers concurrently.
    Optimized for agent usage: uses Tavily, Serper, SerpApi, and falls back to DuckDuckGo.
    """
    funcs = []
    
    # 优先加入速度快、内容丰富的 SerpApi 和 Tavily
    if os.getenv("SERPAPI_KEY"):
        funcs.append(_search_serpapi)
    if os.getenv("TAVILY_API_KEY"):
        funcs.append(_search_tavily)
        
    if os.getenv("SERPER_API_KEY") and not os.getenv("SERPAPI_KEY"):
        funcs.append(_search_serper)
        
    all_docs: List[WebDocument] = []
    
    if funcs:
        provider_results: Dict[Any, List[WebDocument]] = {func: [] for func in funcs}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(funcs) * 2) as executor:
            future_to_func = {}
            for func in funcs:
                future = executor.submit(func, query, max_results, timeout_s)
                future_to_func[future] = func
                
            # 设置总的超时时间，防止被某个很慢的 API 阻塞太久
            done, not_done = concurrent.futures.wait(
                future_to_func.keys(), 
                timeout=timeout_s + 2
            )
            
            for future in done:
                func = future_to_func[future]
                try:
                    docs = future.result()
                    if docs:
                        provider_results[func] = docs
                except Exception as e:
                    logger.warning(f"Provider failed: {e}")
                    
            for future in not_done:
                logger.warning(f"Provider {future_to_func[future].__name__} timed out")
                future.cancel()
                    
        # Interleave results to maintain relevance priority from each provider
        max_len = max((len(docs) for docs in provider_results.values()), default=0)
        for i in range(max_len):
            for func in funcs: # Use original funcs order for priority
                docs = provider_results[func]
                if i < len(docs):
                    all_docs.append(docs[i])
    else:
        # Fallback if no keys are set
        all_docs = _search_duckduckgo(query, max_results)

    # Deduplicate by URL (and title) preserving the interleaved order
    seen_urls: set[str] = set()
    unique_docs: List[WebDocument] = []
    
    for d in all_docs:
        key = d.url if d.url else d.title
        if not key or key in seen_urls:
            continue
        seen_urls.add(key)
        unique_docs.append(d)
        
    # Limit to max_results before returning
    final_docs = unique_docs[:max_results]

    return final_docs


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

