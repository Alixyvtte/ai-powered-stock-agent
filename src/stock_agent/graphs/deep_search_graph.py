from __future__ import annotations

import json
import logging
import os
import time
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
    market: Dict[str, Any]
    need_more: bool
    followup_queries: List[str]
    missing_angles: List[str]
    final_report: str


_logger = logging.getLogger("stock_agent.trace")


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
            "2) Provide 6-10 subqueries that cover: business & competition, financials & valuation, catalysts,\n"
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
        subqueries = plan.subqueries[:10]
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
        for sq in active_queries:
            docs = web_search(sq, max_results=max_results, timeout_s=cfg.timeout_s)
            all_docs.extend(docs)
        picked = pick_best_docs(all_docs, limit=12)
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
        sources = state.get("sources") or []
        notes_existing = state.get("notes") or []
        seen_source_ids = {n.get("source_id") for n in notes_existing}
        batch = [
            s
            for s in sources
            if s.get("id") not in seen_source_ids and (s.get("content") or "").strip()
        ][:8]
        if not batch:
            _logger.info("extract:done seconds=%.2f batch=0", time.time() - t0)
            return {}
        prompt = (
            "Extract verifiable evidence bullets from the web snippets below.\n"
            "Each evidence item must reference a source_id.\n"
            "Rules:\n"
            "- claim: a checkable fact or a specific, attributable viewpoint (avoid vague statements)\n"
            "- why_it_matters: explain the investment relevance (catalyst/risk/financial/competitive, etc.)\n"
            "- Max 2 items per source.\n"
            "Write in English.\n"
            "Sources:\n"
        )
        for s in batch:
            prompt += (
                f"\n[source_id={s['id']}] {s.get('title','')}\n"
                f"URL: {s.get('url','')}\n"
                f"CONTENT:\n{s.get('content','')}\n"
            )
        try:
            out = (
                llm.with_structured_output(EvidenceNotes).invoke(prompt)
                if use_structured
                else _invoke_model_json(llm, EvidenceNotes, prompt)
            )
        except Exception:
            out = EvidenceNotes(items=[])
        merged = notes_existing + [n.model_dump() for n in out.items]
        _logger.info("extract:done seconds=%.2f new_notes=%d total_notes=%d", time.time() - t0, len(out.items), len(merged))
        return {"notes": merged}

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
            "If insufficient, provide 3-6 followup_queries to fill the gaps (e.g., latest earnings call highlights,\n"
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
