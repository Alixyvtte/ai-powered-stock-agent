from __future__ import annotations

import json
import logging
import os
import time
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field, ValidationError

from ..config import AgentConfig
from ..llm import get_chat_model
from ..tools.market_data import fetch_market_snapshot
from ..tools.web_search import WebDocument, pick_best_docs, web_search


class ResearchPlan(BaseModel):
    topic: str = Field(..., description="A concise research topic")
    tickers: List[str] = Field(default_factory=list, description="Related tickers, e.g., NVDA, AAPL")
    subqueries: List[str] = Field(
        default_factory=list,
        description="Search sub-queries (English preferred)",
    )
    assumptions: List[str] = Field(default_factory=list, description="Key assumptions / uncertainties to validate")


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
    final_report: str


_logger = logging.getLogger("stock_agent.trace")
_EXTRACT_BATCH_SIZE = 8
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


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


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
    use_structured = not bool(os.getenv("DEEPSEEK_API_KEY"))

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
                    f"{q} latest earnings call transcript key takeaways",
                    f"{q} revenue drivers demand outlook next 12 months",
                    f"{q} margins gross margin drivers cost headwinds",
                    f"{q} valuation multiples peers forward PE EV EBITDA",
                    f"{q} key catalysts next 6-12 months product roadmap",
                    f"{q} key risks regulation litigation supply chain",
                    f"{q} customers concentration hyperscalers exposure",
                    f"{q} competitors market share AMD Intel Apple",
                    f"{q} macro factors industry cycle inventory",
                ],
                assumptions=[
                    "Evidence may be incomplete; validate with primary filings and earnings transcripts.",
                    "Separate near-term catalysts from long-term narratives; confirm timing and probability.",
                    "Cross-check key claims across multiple independent sources.",
                ],
            )
        subqueries = plan.subqueries[:4]
        _logger.info("plan:done seconds=%.2f subqueries=%d", time.time() - t0, len(subqueries))
        return {
            "plan": plan.model_dump(),
            "subqueries": subqueries,
            "iteration": 0,
            "need_more": False,
            "followup_queries": [],
            "missing_angles": [],
            "sources": [],
            "notes": [],
            "processed_source_ids": [],
        }

    def market_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        _logger.info("market:start")
        plan = ResearchPlan.model_validate(state.get("plan") or {})
        tickers = [t.strip().upper() for t in plan.tickers if t and t.strip()]
        if not tickers:
            _logger.info("market:done seconds=%.2f ticker=none", time.time() - t0)
            return {"market": {}}
        ticker = tickers[0]
        snap = fetch_market_snapshot(ticker)
        _logger.info("market:done seconds=%.2f ticker=%s", time.time() - t0, ticker)
        return {"market": snap.__dict__}

    def search_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        iteration = int(state.get("iteration") or 0)
        subqueries = list(state.get("subqueries") or [])
        followups = list(state.get("followup_queries") or [])
        active_queries = followups if iteration > 0 and followups else subqueries
        _logger.info("search_web:start iteration=%d queries=%d", iteration, len(active_queries))
        max_results = cfg.max_results_per_query
        all_docs: List[WebDocument] = []
        
        # 并发执行所有查询，极大地加快 search 阶段的速度
        import concurrent.futures
        
        # 极速模式：一次性并发所有查询，大幅降低超时时间，拿到什么算什么
        # 不再使用休眠和批处理，以最快速度完成该节点
        fast_timeout = min(cfg.timeout_s, 25) # 搜索最多给 25 秒
        
        # 即使是被超时截断的数据，我们也会尝试从中挑出最好的一批
        # 所以不需要等待全部完成
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(active_queries))) as executor:
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
        batch = sorted(eligible, key=_source_priority)[:_EXTRACT_BATCH_SIZE]
        if not batch:
            _logger.info("extract:done seconds=%.2f batch=0", time.time() - t0)
            return {}
        accepted_notes: List[Dict[str, Any]] = []

        for source in batch:
            source_id = int(source["id"])
            title = str(source.get("title") or "")
            try:
                out = _extract_source_notes(
                    llm,
                    use_structured=use_structured,
                    topic=plan.topic or (state.get("query") or "Equity research"),
                    source=source,
                )
            except Exception as exc:
                _logger.warning("extract: source_id=%s failed: %s", source_id, exc)
                out = EvidenceNotes(items=[])

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
        _logger.info("decide:start iteration=%d sources=%d notes=%d", iteration, len(sources), len(notes))
        prompt = (
            "You are a rigorous research assistant. Decide whether the current evidence is sufficient for a high-quality research memo.\n"
            "If insufficient, provide 1-3 followup_queries to fill the gaps (e.g., latest earnings call highlights,\n"
            "regulation, key customers/supply chain, competitors, valuation anchors).\n"
            "Do NOT repeat existing subqueries.\n"
            "Write in English.\n"
            f"topic: {plan.topic}\n"
            f"subqueries: {plan.subqueries}\n"
            f"market_snapshot: {market}\n"
            f"sources_count: {len(sources)} notes_count: {len(notes)}\n"
            "notes (up to 20):\n"
        )
        for n in notes[:20]:
            prompt += f"- (source {n.get('source_id')}) {n.get('claim')}\n"
        try:
            decision = (
                llm.with_structured_output(FollowupDecision).invoke(prompt)
                if use_structured
                else _invoke_model_json(llm, FollowupDecision, prompt)
            )
        except Exception:
            decision = FollowupDecision(need_more=False, followup_queries=[], missing_angles=["structured parsing failed"])
        next_iteration = iteration + 1
        _logger.info(
            "decide:done seconds=%.2f need_more=%s followups=%d",
            time.time() - t0,
            bool(decision.need_more),
            len(decision.followup_queries),
        )
        return {
            "need_more": bool(decision.need_more),
            "followup_queries": decision.followup_queries,
            "missing_angles": decision.missing_angles,
            "iteration": next_iteration,
        }

    def write_node(state: DeepSearchState) -> DeepSearchState:
        t0 = time.time()
        _logger.info("write_report:start")
        plan = ResearchPlan.model_validate(state.get("plan") or {})
        notes = state.get("notes") or []
        sources = state.get("sources") or []
        market = state.get("market") or {}
        prompt = (
            "You are an equity research analyst. Write a structured research memo in English based on the evidence.\n"
            "Rules:\n"
            "1) Use only the provided sources/notes. Do NOT fabricate.\n"
            "2) Add citations like [S#] after key statements (e.g., [S3]).\n"
            "3) Must include: Executive Summary, Bull Case, Bear Case, Key Catalysts, Key Risks, Open Questions, Sources.\n"
            "4) End with: For research purposes only. Not investment advice.\n"
            f"topic: {plan.topic}\n"
            f"assumptions: {plan.assumptions}\n"
            f"market_snapshot: {market}\n"
            "notes:\n"
        )
        for n in notes[:30]:
            prompt += f"- (S{n.get('source_id')}) {n.get('claim')} | {n.get('why_it_matters')}\n"
        prompt += "\nSources:\n"
        for s in sources[:25]:
            prompt += f"- [S{s.get('id')}] {s.get('title')} {s.get('url')}\n"
        report = llm.invoke(prompt).content
        _logger.info("write_report:done seconds=%.2f chars=%d", time.time() - t0, len(str(report)))
        return {"final_report": str(report)}

    def route_after_decide(state: DeepSearchState) -> str:
        need_more = bool(state.get("need_more"))
        iteration = int(state.get("iteration") or 0)
        if need_more and iteration < int(state.get("max_iterations") or cfg.max_iterations):
            return "search_web"
        return "write_report"

    g = StateGraph(DeepSearchState)
    g.add_node("plan", plan_node)
    g.add_node("market", market_node)
    g.add_node("search_web", search_node)
    g.add_node("extract", extract_node)
    g.add_node("decide", decide_node)
    g.add_node("write_report", write_node)

    g.set_entry_point("plan")
    g.add_edge("plan", "market")
    g.add_edge("market", "search_web")
    g.add_edge("search_web", "extract")
    g.add_edge("extract", "decide")
    g.add_conditional_edges("decide", route_after_decide, {"search_web": "search_web", "write_report": "write_report"})
    g.add_edge("write_report", END)

    return g.compile()
