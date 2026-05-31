from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field, ValidationError

from .. import cache
from ..config import AgentConfig
from ..llm import get_chat_model
from ..tools.content_fetch import fetch_readable_text
from ..tools.market_data import fetch_a_share_snapshot, fetch_market_snapshot
from ..tools.web_search import WebDocument, pick_best_docs, web_search


class ResearchPlan(BaseModel):
    topic: str = Field(..., description="A concise research topic")
    tickers: List[str] = Field(default_factory=list, description="Related tickers, e.g., NVDA, AAPL")
    subqueries: List[str] = Field(
        default_factory=list,
        description="Search sub-queries (English preferred)",
    )
    assumptions: List[str] = Field(default_factory=list, description="Key assumptions / uncertainties to validate")
    market_type: str = Field(
        default="unknown",
        description=(
            "Market type of the query: 'us_equity' for US-listed stocks, "
            "'a_share' for Chinese A-share stocks (SSE/SZSE), "
            "'macro' for macroeconomic/rate/currency questions, "
            "'multi_market' if the query spans multiple markets, "
            "or 'unknown' if unclear."
        ),
    )


class EvidenceNote(BaseModel):
    source_id: int
    claim: str
    why_it_matters: str


class EvidenceNotes(BaseModel):
    items: List[EvidenceNote] = Field(default_factory=list)


class FollowupDecision(BaseModel):
    need_more: bool
    followup_queries: List[str] = Field(default_factory=list)
    missing_angles: List[str] = Field(default_factory=list)
    evidence_confidence: str = Field(
        default="medium",
        description="Overall evidence quality: 'high', 'medium', 'low', or 'insufficient'.",
    )
    refusal_reason: str = Field(
        default="",
        description="If evidence_confidence is 'insufficient', explain why a responsible report cannot be written.",
    )


class VerificationResult(BaseModel):
    passed: bool = Field(default=True, description="Whether the memo is well-supported and complete.")
    issues: List[str] = Field(
        default_factory=list,
        description="Concrete problems: unsupported claims, missing sections, contradictions.",
    )


_CITATION_RE = re.compile(r"\[S(\d+)\]")


def _cited_ids(text: str) -> set[int]:
    return {int(m) for m in _CITATION_RE.findall(text or "")}


def _sanitize_citations(text: str, valid_ids: set[int]) -> tuple[str, int]:
    """Drop [S#] markers that reference a non-existent source. Returns (clean, removed)."""
    removed = 0

    def _replace(match: "re.Match[str]") -> str:
        nonlocal removed
        if int(match.group(1)) in valid_ids:
            return match.group(0)
        removed += 1
        return ""

    cleaned = _CITATION_RE.sub(_replace, text or "")
    # tidy doubled spaces left by removed markers
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, removed


class InvestmentThesis(BaseModel):
    verdict: str = Field(
        default="neutral",
        description="Overall stance: 'bullish', 'bearish', or 'neutral'.",
    )
    conviction: str = Field(
        default="low",
        description="Strength of the view given the evidence: 'high', 'medium', or 'low'.",
    )
    bull_points: List[str] = Field(default_factory=list, description="Key supporting points (cite [S#]).")
    bear_points: List[str] = Field(default_factory=list, description="Key opposing points / risks (cite [S#]).")
    valuation_view: str = Field(default="", description="One-line valuation read vs. fundamentals/peers.")
    price_view: str = Field(default="", description="Brief view on price vs. analyst target / 200-day average.")


class DeepSearchState(TypedDict, total=False):
    query: str
    iteration: int
    max_iterations: int
    plan: Dict[str, Any]
    subqueries: List[str]
    sources: List[Dict[str, Any]]
    notes: List[Dict[str, Any]]
    processed_source_ids: List[int]
    market: Dict[str, Any]
    need_more: bool
    followup_queries: List[str]
    missing_angles: List[str]
    all_missing_angles: List[str]
    evidence_confidence: str
    research_timestamp: str
    language: str
    thesis: Dict[str, Any]
    final_report: str
    verification: Dict[str, Any]
    verify_attempts: int
    verify_feedback: str


_logger = logging.getLogger("stock_agent.trace")
_MAX_NOTES_PER_SOURCE = 2
_HIGH_SIGNAL_HOST_HINTS = (
    "sec.gov",
    "investor",
    "ir.",
    "newsroom",
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "apnews.com",
    "earningscalltranscript.com",
)
_HIGH_SIGNAL_TEXT_HINTS = (
    "earnings call",
    "transcript",
    "shareholder letter",
    "form 10-k",
    "form 10-q",
    "annual report",
    "quarterly report",
    "press release",
)
_LOW_SIGNAL_HINTS = (
    "motleyfool",
    "fool.com",
    "zacks.com",
    "investorplace.com",
    "tipranks.com",
    "list of",
    "top stocks",
    "best stocks",
    "price prediction",
)
_VAGUE_CLAIM_PREFIXES = (
    "the article",
    "this article",
    "the source",
    "this source",
    "the page",
    "this page",
    "the report",
    "this report",
)
_GENERIC_CLAIMS = {
    "evidence",
    "analysis",
    "research",
    "update",
    "news",
}


def _truncate(text: str, limit: int = 2200) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "..."


def _extract_json_object(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        fence = t.rfind("```")
        if fence != -1:
            t = t[:fence]
        t = t.strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return a JSON object")
    return t[start : end + 1]


def _invoke_model_json(llm, schema: type[BaseModel], prompt: str) -> BaseModel:
    required = list(schema.model_json_schema().get("required", []))
    rules = (
        "Return ONLY a single JSON object.\n"
        "No markdown. No code fences. No surrounding text.\n"
        f"Schema name: {schema.__name__}\n"
        f"Required keys: {required}\n"
        "If unknown, use empty string or empty list as appropriate.\n"
    )

    last_err = None
    last_raw = None
    for _ in range(3):
        wrapped = f"{prompt}\n\n{rules}"
        if last_err and last_raw:
            wrapped += (
                "\nYour previous output was invalid.\n"
                f"Error: {last_err}\n"
                "Fix the JSON to satisfy the required keys and types.\n"
                f"Previous output (truncated): {str(last_raw)[:1200]}\n"
            )
        raw = llm.invoke(wrapped).content
        last_raw = raw
        try:
            obj = _extract_json_object(str(raw))
            return schema.model_validate(json.loads(obj))
        except (ValueError, json.JSONDecodeError, ValidationError) as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"Failed to parse valid JSON for {schema.__name__}: {last_err}")


_PRIMARY_DOMAINS = frozenset({
    "sec.gov", "fred.stlouisfed.org", "bea.gov", "ecb.europa.eu",
    "stats.bis.org", "csrc.gov.cn", "cninfo.com.cn", "sse.com.cn", "szse.cn",
})
_SECONDARY_DOMAINS = frozenset({
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
    "cls.cn", "cs.com.cn", "cnstock.com",
})


def _classify_source(url: str) -> str:
    if not url:
        return "aggregator"
    for d in _PRIMARY_DOMAINS:
        if d in url:
            return "primary"
    for d in _SECONDARY_DOMAINS:
        if d in url:
            return "secondary"
    return "aggregator"
def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _detect_language(text: str) -> str:
    """Pick the report language from the query: Chinese if it contains CJK, else English."""
    if text and any("一" <= ch <= "鿿" for ch in text):
        return "Chinese"
    return "English"


def _source_host(url: str) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().removeprefix("www.")


def _source_priority(source: Dict[str, Any]) -> tuple[int, int]:
    source_id = int(source.get("id") or 0)
    host = _source_host(str(source.get("url") or ""))
    title = (source.get("title") or "").lower()
    content = (source.get("content") or "").lower()[:500]
    blob = f"{host} {title} {content}"
    score = 0

    if host and any(hint in host for hint in _HIGH_SIGNAL_HOST_HINTS):
        score += 4
    if any(hint in blob for hint in _HIGH_SIGNAL_TEXT_HINTS):
        score += 2
    if host and any(hint in host for hint in _LOW_SIGNAL_HINTS):
        score -= 3
    if any(hint in blob for hint in _LOW_SIGNAL_HINTS):
        score -= 2
    if source.get("url"):
        score += 1
    if len((source.get("content") or "").strip()) < 120:
        score -= 1

    return (-score, source_id)


def _is_meaningful_note(claim: str, why_it_matters: str, title: str) -> bool:
    claim_norm = _normalize_text(claim)
    why_norm = _normalize_text(why_it_matters)
    title_norm = _normalize_text(title)

    if not claim_norm or not why_norm:
        return False
    if len(claim_norm) < 18 or len(claim_norm.split()) < 4:
        return False
    if len(why_norm) < 12 or len(why_norm.split()) < 3:
        return False
    if claim_norm in _GENERIC_CLAIMS or claim_norm == title_norm:
        return False
    if any(claim_norm.startswith(prefix) for prefix in _VAGUE_CLAIM_PREFIXES):
        return False
    return True


def _extract_source_notes(
    llm: Any,
    *,
    use_structured: bool,
    topic: str,
    source: Dict[str, Any],
) -> EvidenceNotes:
    prompt = (
        "You are extracting evidence for an equity research workflow.\n"
        f"Research topic: {topic}\n"
        "Review exactly one source and return up to 2 evidence items for that source only.\n"
        "Rules:\n"
        "- Keep source_id exactly as provided.\n"
        "- claim must be a concrete, checkable fact or a specific attributable viewpoint from the source.\n"
        "- why_it_matters must explain the investing relevance in one sentence.\n"
        "- Do not restate the page title or provide a generic summary.\n"
        "- If the source is off-topic, low-signal, or has no usable evidence, return items as an empty list.\n"
        "Write in English.\n"
        "Source:\n"
        f"[source_id={source['id']}] {source.get('title', '')}\n"
        f"URL: {source.get('url', '')}\n"
        f"CONTENT:\n{source.get('content', '')}\n"
    )
    if use_structured:
        return llm.with_structured_output(EvidenceNotes).invoke(prompt)
    return _invoke_model_json(llm, EvidenceNotes, prompt)


def build_deep_search_graph(config: Optional[AgentConfig] = None):
    cfg = config or AgentConfig.from_env()
    llm = get_chat_model(cfg)
    use_structured = cfg.use_structured_output

    def plan_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        q = state["query"]
        _logger.info("plan:start")
        prompt = (
            "You are an equity research analyst. Convert the user's question into an executable research plan.\n"
            "Requirements:\n"
            "1) Output must strictly follow the structured schema fields.\n"
            "2) Provide 3-5 subqueries that cover: business & competition, financials & valuation, catalysts,\n"
            "   risks, regulation/litigation, macro & industry.\n"
            "3) tickers can be empty if uncertain.\n"
            "4) Provide 3-6 key assumptions / uncertainties to validate.\n"
            "5) Set market_type: 'us_equity' for US-listed stocks, 'a_share' for Chinese A-share stocks\n"
            "   (SSE/SZSE, 6-digit codes or Chinese company names listed domestically), 'macro' for\n"
            "   macroeconomic/rate/currency questions, 'multi_market' if the query spans multiple markets,\n"
            "   or 'unknown' if unclear.\n"
            "Output in English.\n"
            f"User question: {q}"
        )
        try:
            plan = (
                llm.with_structured_output(ResearchPlan).invoke(prompt)
                if use_structured
                else _invoke_model_json(llm, ResearchPlan, prompt)
            )
        except Exception:
            plan = ResearchPlan(
                topic=q.strip() or "Equity research",
                tickers=[],
                subqueries=[
                    f"{q} business overview competitive landscape",
                    f"{q} latest earnings financial results key takeaways",
                    f"{q} revenue growth drivers demand outlook next 12 months",
                    f"{q} gross margin cost structure headwinds tailwinds",
                    f"{q} valuation multiples peers forward PE EV EBITDA",
                    f"{q} key catalysts next 6-12 months product roadmap",
                    f"{q} key risks regulatory litigation supply chain",
                    f"{q} competitor market share industry dynamics",
                    f"{q} macro factors sector cycle trends",
                ],
                assumptions=[
                    "Evidence may be incomplete; validate with primary filings and earnings transcripts.",
                    "Separate near-term catalysts from long-term narratives; confirm timing and probability.",
                    "Cross-check key claims across multiple independent sources.",
                ],
            )
        # Queries run concurrently, so a slightly wider set improves coverage at
        # negligible latency cost (downstream doc selection is still capped).
        subqueries = plan.subqueries[:6]
        research_timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        _logger.info("plan:done seconds=%.2f subqueries=%d market_type=%s", time.time() - t0, len(subqueries), plan.market_type)
        return {
            "plan": plan.model_dump(),
            "subqueries": subqueries,
            "iteration": 0,
            "need_more": False,
            "followup_queries": [],
            "missing_angles": [],
            "all_missing_angles": [],
            "evidence_confidence": "medium",
            "research_timestamp": research_timestamp,
            "language": _detect_language(q),
            "sources": [],
            "notes": [],
            "processed_source_ids": [],
        }

    def market_node(state: DeepSearchState) -> DeepSearchState:
        import concurrent.futures
        t0 = time.time()
        _logger.info("market:start")
        plan = ResearchPlan.model_validate(state.get("plan") or {})
        tickers = [t.strip().upper() for t in plan.tickers if t and t.strip()]
        if not tickers:
            _logger.info("market:done seconds=%.2f ticker=none", time.time() - t0)
            return {"market": {}}

        def _fetch_yf(ticker: str):
            hit = cache.get("market_yf", ticker, cache.TTL_MARKET)
            if hit is not None:
                return "yfinance", ticker, hit
            try:
                data = fetch_market_snapshot(ticker).__dict__
            except Exception as e:
                data = {"ticker": ticker, "error": str(e)}
            if not data.get("error"):
                cache.set("market_yf", ticker, data)
            return "yfinance", ticker, data

        def _fetch_ak(ticker: str):
            hit = cache.get("market_ak", ticker, cache.TTL_MARKET)
            if hit is not None:
                return "akshare", ticker, hit
            try:
                data = fetch_a_share_snapshot(ticker).__dict__
            except Exception as e:
                data = {"ticker": ticker, "error": str(e)}
            if not data.get("error"):
                cache.set("market_ak", ticker, data)
            return "akshare", ticker, data

        market_type = (plan.market_type or "unknown").strip().lower()

        def _fetchers_for(ticker: str):
            # P2 routing: skip the slow akshare scrape for US names.
            #   us_equity -> yfinance only
            #   a_share   -> akshare primary + yfinance fallback
            #   else      -> route by ticker shape (6-digit code = A-share)
            if market_type == "us_equity":
                return (_fetch_yf,)
            if market_type == "a_share":
                return (_fetch_ak, _fetch_yf)
            if re.fullmatch(r"\d{6}", ticker):
                return (_fetch_ak, _fetch_yf)
            return (_fetch_yf,)

        results: Dict[str, Dict[str, Any]] = {t: {} for t in tickers}
        tasks = [(fn, t) for t in tickers for fn in _fetchers_for(t)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as executor:
            future_map = {executor.submit(fn, t): (fn.__name__, t) for fn, t in tasks}
            done, not_done = concurrent.futures.wait(future_map.keys(), timeout=15)
            for future in done:
                try:
                    src, ticker, data = future.result()
                    results[ticker][src] = data
                except Exception as e:
                    _logger.warning("market fetch error: %s", e)
            for future in not_done:
                fn_name, ticker = future_map[future]
                _logger.warning("market fetch timed out: %s %s", fn_name, ticker)
                future.cancel()

        _logger.info("market:done seconds=%.2f tickers=%s", time.time() - t0, list(results.keys()))
        return {"market": results}

    def search_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        iteration = int(state.get("iteration") or 0)
        subqueries = list(state.get("subqueries") or [])
        followups = list(state.get("followup_queries") or [])
        active_queries = followups if iteration > 0 and followups else subqueries
        _logger.info("search_web:start iteration=%d queries=%d", iteration, len(active_queries))
        max_results = cfg.max_results_per_query
        all_docs: List[WebDocument] = []

        # Run all queries concurrently and hard-cap the wait so one slow provider
        # never stalls the node; whatever returned in time is ranked downstream.
        # The timeout follows the active preset (fast/standard/deep).
        import concurrent.futures

        fast_timeout = cfg.timeout_s
        workers = min(16, max(1, len(active_queries)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_query = {
                executor.submit(web_search, sq, max_results=max_results, timeout_s=fast_timeout): sq
                for sq in active_queries
            }
            
            # 使用带超时的 wait，强制截断
            done, not_done = concurrent.futures.wait(
                future_to_query.keys(),
                timeout=fast_timeout + 2  # 27 秒内必须结束整个 search_node 的并发
            )
            
            for future in done:
                try:
                    docs = future.result()
                    all_docs.extend(docs)
                except Exception as e:
                    _logger.warning(f"search_web: query failed: {e}")
                    
            for future in not_done:
                _logger.warning(f"search_web: query timed out: {future_to_query[future]}")
                future.cancel()
                
        picked = pick_best_docs(all_docs, limit=6)
        existing = state.get("sources") or []
        existing_urls = {s.get("url") for s in existing if s.get("url")}
        new_sources: List[Dict[str, Any]] = []
        next_id = len(existing) + 1
        for d in picked:
            if d.url and d.url in existing_urls:
                continue
            new_sources.append(
                {
                    "id": next_id,
                    "title": d.title,
                    "url": d.url,
                    "content": _truncate(d.content, 2400),
                }
            )
            next_id += 1
        _logger.info(
            "search_web:done seconds=%.2f new_sources=%d total_sources=%d",
            time.time() - t0,
            len(new_sources),
            len(existing) + len(new_sources),
        )
        return {"sources": existing + new_sources}

    def fetch_content_node(state: DeepSearchState) -> DeepSearchState:
        """Enrich the top sources with full readable page text (snippets -> body).

        Bounded to cfg.fetch_top_n (preset-driven: fast=0/off, standard=3, deep=6),
        fetched concurrently, cached, and gated so a fetch failure never blocks
        extraction. Already-fetched sources are skipped across loop iterations.
        """
        t0 = time.time()
        top_n = int(cfg.fetch_top_n or 0)
        sources = state.get("sources") or []
        if top_n <= 0 or not sources:
            _logger.info("fetch_content:skip top_n=%d", top_n)
            return {}

        candidates = [s for s in sources if s.get("url") and not s.get("fetched")]
        candidates = sorted(candidates, key=_source_priority)[:top_n]
        if not candidates:
            _logger.info("fetch_content:done seconds=%.2f fetched=0", time.time() - t0)
            return {}

        import concurrent.futures
        fetch_timeout = min(cfg.timeout_s, 12)

        def _enrich(source: Dict[str, Any]):
            return int(source["id"]), fetch_readable_text(
                str(source.get("url") or ""), timeout_s=fetch_timeout, max_chars=5000
            )

        fetched: Dict[int, str] = {}
        workers = max(1, min(8, len(candidates)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for source_id, text in executor.map(_enrich, candidates):
                fetched[source_id] = text

        enriched = 0
        new_sources: List[Dict[str, Any]] = []
        for source in sources:
            source_id = int(source.get("id") or 0)
            if source_id not in fetched:
                new_sources.append(source)
                continue
            updated = dict(source)
            updated["fetched"] = True  # mark attempted to avoid refetching in loops
            text = fetched[source_id]
            if text and len(text) > len(str(source.get("content") or "")):
                updated["content"] = text
                enriched += 1
            new_sources.append(updated)

        _logger.info(
            "fetch_content:done seconds=%.2f fetched=%d enriched=%d",
            time.time() - t0, len(candidates), enriched,
        )
        return {"sources": new_sources}

    def extract_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        _logger.info("extract:start")
        plan_data = state.get("plan") or {"topic": state.get("query") or "Equity research"}
        plan = ResearchPlan.model_validate(plan_data)
        sources = state.get("sources") or []
        notes_existing = state.get("notes") or []
        processed_source_ids = [int(v) for v in (state.get("processed_source_ids") or []) if v is not None]
        if not processed_source_ids:
            processed_source_ids = []
            for note in notes_existing:
                source_id = note.get("source_id")
                if source_id is None:
                    continue
                source_id = int(source_id)
                if source_id not in processed_source_ids:
                    processed_source_ids.append(source_id)
        processed_source_id_set = set(processed_source_ids)
        existing_claim_keys = {
            _normalize_text(str(n.get("claim") or ""))
            for n in notes_existing
            if _normalize_text(str(n.get("claim") or ""))
        }
        eligible = [
            s
            for s in sources
            if s.get("id") not in processed_source_id_set and (s.get("content") or "").strip()
        ]
        batch = sorted(eligible, key=_source_priority)[: cfg.extract_batch_size]
        if not batch:
            _logger.info("extract:done seconds=%.2f batch=0", time.time() - t0)
            return {}
        accepted_notes: List[Dict[str, Any]] = []
        topic = plan.topic or (state.get("query") or "Equity research")

        # P1: fetch evidence for all sources concurrently. The LLM call is the
        # slow part, so running the batch in parallel turns "sum of N calls"
        # into "slowest single call". Validation / dedup / merge below still run
        # in deterministic batch order, so note ordering and processed-id
        # tracking stay identical to the sequential version.
        def _extract_one(source: Dict[str, Any]) -> EvidenceNotes:
            try:
                return _extract_source_notes(
                    llm, use_structured=use_structured, topic=topic, source=source
                )
            except Exception as exc:
                _logger.warning("extract: source_id=%s failed: %s", source.get("id"), exc)
                return EvidenceNotes(items=[])

        workers = max(1, min(len(batch), cfg.extract_max_workers))
        if workers > 1:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                outputs = list(executor.map(_extract_one, batch))
        else:
            outputs = [_extract_one(source) for source in batch]

        for source, out in zip(batch, outputs):
            source_id = int(source["id"])
            title = str(source.get("title") or "")
            for note in out.items:
                claim = (note.claim or "").strip()
                why_it_matters = (note.why_it_matters or "").strip()
                claim_key = _normalize_text(claim)
                if note.source_id != source_id:
                    continue
                if not _is_meaningful_note(claim, why_it_matters, title):
                    continue
                if not claim_key or claim_key in existing_claim_keys:
                    continue
                existing_claim_keys.add(claim_key)
                accepted_notes.append(
                    {
                        "source_id": source_id,
                        "claim": claim,
                        "why_it_matters": why_it_matters,
                    }
                )
                if sum(1 for n in accepted_notes if n["source_id"] == source_id) >= _MAX_NOTES_PER_SOURCE:
                    break

            if source_id not in processed_source_id_set:
                processed_source_ids.append(source_id)
                processed_source_id_set.add(source_id)

        merged = notes_existing + accepted_notes
        _logger.info(
            "extract:done seconds=%.2f processed=%d new_notes=%d total_notes=%d",
            time.time() - t0,
            len(batch),
            len(accepted_notes),
            len(merged),
        )
        return {
            "notes": merged,
            "processed_source_ids": processed_source_ids,
        }

    def decide_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        iteration = int(state.get("iteration") or 0)
        plan = ResearchPlan.model_validate(state.get("plan") or {})
        notes = state.get("notes") or []
        sources = state.get("sources") or []
        market = state.get("market") or {}
        prev_followups = list(state.get("followup_queries") or [])
        prev_missing = list(state.get("all_missing_angles") or [])
        # Classify sources by credibility tier
        tier_counts: Dict[str, int] = {"primary": 0, "secondary": 0, "aggregator": 0}
        for s in sources:
            tier_counts[_classify_source(s.get("url", ""))] += 1

        _logger.info("decide:start iteration=%d sources=%d notes=%d primary=%d",
                     iteration, len(sources), len(notes), tier_counts["primary"])
        prompt = (
            "You are a rigorous research assistant. Decide whether the current evidence is sufficient for a high-quality research memo.\n"
            "If insufficient, provide 1-3 followup_queries to fill the gaps.\n"
            "Do NOT repeat any query listed in already_searched_followups or initial_subqueries.\n"
            "Set evidence_confidence to one of: 'high' (well-supported by primary/secondary sources),\n"
            "  'medium' (some support but gaps remain), 'low' (mostly aggregators, thin coverage),\n"
            "  'insufficient' (cannot write a responsible memo — set need_more=false and explain in refusal_reason).\n"
            "Primary sources (sec.gov, csrc.gov.cn, cninfo.com.cn, fred.stlouisfed.org, etc.) carry the most weight.\n"
            "Write in English.\n"
            f"topic: {plan.topic}\n"
            f"initial_subqueries: {plan.subqueries}\n"
            f"already_searched_followups: {prev_followups}\n"
            f"key_assumptions_to_validate: {plan.assumptions}\n"
            f"previously_identified_gaps: {prev_missing}\n"
            f"market_snapshot: {market}\n"
            f"sources_by_tier: primary={tier_counts['primary']} secondary={tier_counts['secondary']} aggregator={tier_counts['aggregator']}\n"
            f"total_notes: {len(notes)}\n"
            "For each key assumption, check if the existing notes provide sufficient evidence. "
            "List unvalidated assumptions as missing_angles.\n"
            "notes (up to 20):\n"
        )
        for n in notes[:20]:
            src_tier = _classify_source(
                next((s.get("url", "") for s in sources if s.get("id") == n.get("source_id")), "")
            )
            prompt += f"- [{src_tier}] (source {n.get('source_id')}) {n.get('claim')} | relevance: {n.get('why_it_matters')}\n"
        try:
            decision = (
                llm.with_structured_output(FollowupDecision).invoke(prompt)
                if use_structured
                else _invoke_model_json(llm, FollowupDecision, prompt)
            )
        except Exception:
            decision = FollowupDecision(
                need_more=False,
                followup_queries=[],
                missing_angles=["structured parsing failed"],
                evidence_confidence="low",
            )
        next_iteration = iteration + 1
        new_missing = decision.missing_angles
        _logger.info(
            "decide:done seconds=%.2f need_more=%s followups=%d confidence=%s",
            time.time() - t0,
            bool(decision.need_more),
            len(decision.followup_queries),
            decision.evidence_confidence,
        )
        return {
            "need_more": bool(decision.need_more),
            "followup_queries": decision.followup_queries,
            "missing_angles": new_missing,
            "all_missing_angles": prev_missing + new_missing,
            "evidence_confidence": decision.evidence_confidence,
            "iteration": next_iteration,
        }

    def synthesize_node(state: DeepSearchState) -> DeepSearchState:
        """Form a structured investment thesis (verdict / conviction / bull / bear /
        valuation) BEFORE writing, so the memo leads with a judgment, not prose."""
        t0 = time.time()
        _logger.info("synthesize:start")
        plan = ResearchPlan.model_validate(state.get("plan") or {})
        notes = state.get("notes") or []
        market = state.get("market") or {}
        language = state.get("language") or "English"
        evidence_confidence = state.get("evidence_confidence") or "medium"
        prompt = (
            "You are the lead analyst forming an investment thesis from the gathered evidence.\n"
            "Decide an overall verdict (bullish/bearish/neutral) and conviction (high/medium/low) "
            "that is consistent with the strength of the evidence.\n"
            "Provide 2-5 bull_points and 2-5 bear_points, each citing [S#] where possible.\n"
            "Give a one-line valuation_view and a one-line price_view grounded in the market_snapshot.\n"
            "Do NOT fabricate; base everything on the notes and market data.\n"
            f"Write bull_points / bear_points / valuation_view / price_view in {language}; "
            "keep verdict and conviction as the English enum values.\n"
            f"topic: {plan.topic}\n"
            f"evidence_confidence: {evidence_confidence}\n"
            f"market_snapshot: {market}\n"
            "notes (up to 30):\n"
        )
        for n in notes[:30]:
            prompt += f"- (S{n.get('source_id')}) {n.get('claim')} | {n.get('why_it_matters')}\n"
        try:
            thesis = (
                llm.with_structured_output(InvestmentThesis).invoke(prompt)
                if use_structured
                else _invoke_model_json(llm, InvestmentThesis, prompt)
            )
        except Exception:
            thesis = InvestmentThesis(verdict="neutral", conviction="low")
        _logger.info(
            "synthesize:done seconds=%.2f verdict=%s conviction=%s",
            time.time() - t0, thesis.verdict, thesis.conviction,
        )
        return {"thesis": thesis.model_dump()}

    def write_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        _logger.info("write_report:start")
        plan = ResearchPlan.model_validate(state.get("plan") or {})
        notes = state.get("notes") or []
        sources = state.get("sources") or []
        market = state.get("market") or {}
        research_timestamp = state.get("research_timestamp") or "unknown"
        evidence_confidence = state.get("evidence_confidence") or "medium"
        language = state.get("language") or "English"
        thesis = state.get("thesis") or {}
        prompt = (
            f"You are an equity research analyst. Write a structured research memo in {language} based on the evidence.\n"
            f"Write the ENTIRE memo in {language}, including section headings and the disclaimer.\n"
            "Rules:\n"
            "1) Use only the provided sources/notes. Do NOT fabricate.\n"
            "2) Add citations like [S#] after key statements (e.g., [S3]). Keep the [S#] markers verbatim.\n"
            f"3) Must include these sections (translate the headings into {language}): "
            "Executive Summary, Bull Case, Bear Case, Key Catalysts, Key Risks, Open Questions, Sources.\n"
            f"4) Begin with a line stating the research timestamp: {research_timestamp}, then open the "
            "Executive Summary by stating the investment_thesis verdict and conviction, and justify it.\n"
            "5) If evidence_confidence is 'low' or 'insufficient', prominently warn (in the report's language) "
            "that evidence coverage is limited and key claims may be unverified.\n"
            "6) End with a research-only / not-investment-advice disclaimer (in the report's language).\n"
            "7) Where relevant, ground valuation, profitability and momentum claims in the "
            "market_snapshot figures (price, market cap, trailing/forward P/E, margins, ROE, "
            "revenue/earnings growth, analyst target vs. price, 200-day average).\n"
            "8) Build Bull Case / Bear Case around the investment_thesis bull_points / bear_points.\n"
            f"topic: {plan.topic}\n"
            f"assumptions: {plan.assumptions}\n"
            f"evidence_confidence: {evidence_confidence}\n"
            f"investment_thesis: {thesis}\n"
            f"market_snapshot: {market}\n"
            "notes:\n"
        )
        for n in notes[:30]:
            prompt += f"- (S{n.get('source_id')}) {n.get('claim')} | {n.get('why_it_matters')}\n"
        prompt += "\nSources:\n"
        for s in sources[:25]:
            prompt += f"- [S{s.get('id')}] {s.get('title')} {s.get('url')}\n"

        verify_feedback = (state.get("verify_feedback") or "").strip()
        if verify_feedback:
            prompt += (
                "\nThis is a REVISION of a prior draft. Fix these issues and re-emit the full memo, "
                "keeping only [S#] citations that map to the Sources list above:\n"
                f"{verify_feedback}\n"
            )

        report = llm.invoke(prompt).content
        _logger.info("write_report:done seconds=%.2f chars=%d", time.time() - t0, len(str(report)))
        return {"final_report": str(report)}

    def verify_node(state: DeepSearchState) -> DeepSearchState:
        """Adversarial self-check after writing: validate [S#] citations against real
        sources (and strip hallucinated ones), then critique for unsupported claims /
        missing sections. If issues remain and a revision budget is left, route back to
        write_report once. fast mode does the deterministic citation check only."""
        t0 = time.time()
        report = str(state.get("final_report") or "")
        sources = state.get("sources") or []
        language = state.get("language") or "English"
        attempts = int(state.get("verify_attempts") or 0)
        valid_ids = {int(s.get("id")) for s in sources if s.get("id") is not None}

        cited = _cited_ids(report)
        invalid = sorted(cited - valid_ids)
        clean_report, removed = _sanitize_citations(report, valid_ids)

        issues: List[str] = []
        if invalid:
            issues.append(f"Citations referencing non-existent sources were removed: {invalid}.")

        passed = True
        if cfg.mode != "fast":
            prompt = (
                "You are a skeptical reviewer. Check the research memo for problems: claims not "
                "supported by the notes, missing required sections (Executive Summary, Bull Case, "
                "Bear Case, Key Catalysts, Key Risks, Open Questions, Sources), or internal "
                "contradictions. List concrete issues; set passed=false only if there are real "
                f"problems. Reason about content regardless of language ({language}).\n"
                f"notes:\n"
            )
            for n in (state.get("notes") or [])[:30]:
                prompt += f"- (S{n.get('source_id')}) {n.get('claim')}\n"
            prompt += f"\nMEMO:\n{clean_report[:6000]}\n"
            try:
                result = (
                    llm.with_structured_output(VerificationResult).invoke(prompt)
                    if use_structured
                    else _invoke_model_json(llm, VerificationResult, prompt)
                )
                passed = bool(result.passed)
                issues.extend(result.issues or [])
            except Exception:
                passed = True  # never block the report on a critique failure

        max_revisions = 0 if cfg.mode == "fast" else 1
        need_revision = (bool(invalid) or not passed) and attempts < max_revisions
        verification = {
            "citations": len(cited),
            "invalid_citations": len(invalid),
            "removed_citations": removed,
            "passed": bool(passed and not invalid),
            "issues": issues[:10],
        }
        _logger.info(
            "verify:done seconds=%.2f citations=%d invalid=%d passed=%s revision=%s",
            time.time() - t0, len(cited), len(invalid), verification["passed"], need_revision,
        )
        return {
            "final_report": clean_report,
            "verification": verification,
            "verify_attempts": attempts + (1 if need_revision else 0),
            "verify_feedback": ("; ".join(issues)[:1000] if need_revision else ""),
        }

    def route_after_verify(state: DeepSearchState) -> str:
        return "write_report" if (state.get("verify_feedback") or "").strip() else "end"

    def route_after_decide(state: DeepSearchState) -> str:
        need_more = bool(state.get("need_more"))
        iteration = int(state.get("iteration") or 0)
        evidence_confidence = state.get("evidence_confidence") or "medium"
        # Insufficient evidence: skip further search and go straight to synthesis,
        # which produces a low-conviction thesis the caveated memo is built on.
        if evidence_confidence == "insufficient":
            return "synthesize"
        if need_more and iteration < int(state.get("max_iterations") or cfg.max_iterations):
            return "search_web"
        return "synthesize"

    g = StateGraph(DeepSearchState)
    g.add_node("plan", plan_node)
    g.add_node("market", market_node)
    g.add_node("search_web", search_node)
    g.add_node("fetch_content", fetch_content_node)
    g.add_node("extract", extract_node)
    g.add_node("decide", decide_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("write_report", write_node)
    g.add_node("verify", verify_node)

    g.set_entry_point("plan")
    # Linear pipeline. (P5 parallel market||search was reverted: once fetch_content
    # was inserted, market->extract became a shorter path than
    # search->fetch_content->extract, and the unequal-length fan-in made extract
    # fire twice and downstream nodes write concurrently. P2 already makes market
    # fast (~3s, US skips akshare), so the lost overlap is negligible.)
    g.add_edge("plan", "market")
    g.add_edge("market", "search_web")
    g.add_edge("search_web", "fetch_content")
    g.add_edge("fetch_content", "extract")
    g.add_edge("extract", "decide")
    # decide loops back to search_web, or proceeds to synthesize -> write_report.
    g.add_conditional_edges("decide", route_after_decide, {"search_web": "search_web", "synthesize": "synthesize"})
    g.add_edge("synthesize", "write_report")
    # write_report -> verify (self-check) -> either revise once or finish.
    g.add_edge("write_report", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"write_report": "write_report", "end": END})

    return g.compile()
