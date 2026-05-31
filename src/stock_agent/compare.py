"""Multi-stock comparison: run the research agent over several tickers and rank them.

Each ticker is analysed concurrently by a full agent run; the resulting investment
theses are scored (verdict x conviction) into a head-to-head ranking with a short
comparison summary. The agent is injectable so this is unit-testable offline.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .agent import DeepSearchAgent
from .config import AgentConfig


_VERDICT_SCORE = {"bullish": 2, "neutral": 1, "bearish": 0}
_CONVICTION_SCORE = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class StockComparison:
    ticker: str
    verdict: str
    conviction: str
    score: float
    valuation_view: str
    price: Optional[float]
    market: Dict[str, Any] = field(default_factory=dict)
    report: str = ""


@dataclass(frozen=True)
class ComparisonResult:
    query: str
    stocks: List[StockComparison]      # ranked best -> worst
    top_pick: Optional[str]
    summary: str


def _score(verdict: str, conviction: str) -> float:
    v = _VERDICT_SCORE.get((verdict or "").lower(), 1)
    c = _CONVICTION_SCORE.get((conviction or "").lower(), 1)
    return float(v * c)


def _market_for(market: Any, ticker: str) -> Dict[str, Any]:
    """Pull the best provider payload for a ticker from the nested market dict."""
    if not isinstance(market, dict):
        return {}
    provider_map = market.get(ticker) or market.get(ticker.upper())
    if not isinstance(provider_map, dict):
        # fall back to the first ticker present
        for value in market.values():
            if isinstance(value, dict):
                provider_map = value
                break
    if not isinstance(provider_map, dict):
        return {}
    for payload in provider_map.values():
        if isinstance(payload, dict) and not payload.get("error"):
            return payload
    return {}


def _summarize(query: str, ranked: List[StockComparison]) -> str:
    if not ranked:
        return "No stocks were compared."
    lines = [f"Comparison for: {query}", ""]
    top = ranked[0]
    lines.append(
        f"Top pick: {top.ticker} — {top.verdict} ({top.conviction} conviction)."
        if top.score > 0
        else f"No clearly favoured name; {top.ticker} ranks first by a thin margin."
    )
    lines.append("")
    lines.append("Ranking:")
    for i, s in enumerate(ranked, 1):
        price = f", price {s.price}" if s.price is not None else ""
        val = f" — {s.valuation_view}" if s.valuation_view else ""
        lines.append(f"{i}. {s.ticker}: {s.verdict} / {s.conviction} conviction{price}{val}")
    lines.append("")
    lines.append("For research purposes only. Not investment advice.")
    return "\n".join(lines)


def compare_stocks(
    query: str,
    tickers: List[str],
    *,
    agent: Optional[DeepSearchAgent] = None,
    config: Optional[AgentConfig] = None,
    max_workers: int = 3,
) -> ComparisonResult:
    """Analyse each ticker concurrently and return a ranked head-to-head comparison."""
    clean = [t.strip().upper() for t in tickers if t and t.strip()]
    if not clean:
        raise ValueError("compare_stocks requires at least one ticker.")
    runner = agent or DeepSearchAgent(config)

    def _run_one(ticker: str) -> StockComparison:
        result = runner.run(f"{query} — focus specifically on {ticker} ({ticker} stock)")
        state = result.state or {}
        thesis = state.get("thesis") or {}
        verdict = str(thesis.get("verdict") or "neutral")
        conviction = str(thesis.get("conviction") or "low")
        payload = _market_for(state.get("market"), ticker)
        price = payload.get("price")
        return StockComparison(
            ticker=ticker,
            verdict=verdict,
            conviction=conviction,
            score=_score(verdict, conviction),
            valuation_view=str(thesis.get("valuation_view") or ""),
            price=price if isinstance(price, (int, float)) else None,
            market=payload,
            report=result.final_report,
        )

    workers = max(1, min(max_workers, len(clean)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        stocks = list(executor.map(_run_one, clean))

    ranked = sorted(stocks, key=lambda s: s.score, reverse=True)
    top_pick = ranked[0].ticker if ranked and ranked[0].score > 0 else (ranked[0].ticker if ranked else None)
    return ComparisonResult(
        query=query,
        stocks=ranked,
        top_pick=top_pick,
        summary=_summarize(query, ranked),
    )


__all__ = ["StockComparison", "ComparisonResult", "compare_stocks"]
