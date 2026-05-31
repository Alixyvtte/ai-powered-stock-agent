from __future__ import annotations

from stock_agent.agent import AgentResult
from stock_agent.compare import compare_stocks, _score


class _FakeAgent:
    """Returns a canned thesis/market per ticker based on the focused query."""

    def __init__(self, by_ticker):
        self._by_ticker = by_ticker

    def run(self, query: str) -> AgentResult:
        ticker = next((t for t in self._by_ticker if t in query), None)
        verdict, conviction, price = self._by_ticker[ticker]
        state = {
            "thesis": {"verdict": verdict, "conviction": conviction, "valuation_view": f"{ticker} view"},
            "market": {ticker: {"yfinance": {"ticker": ticker, "price": price}}},
        }
        return AgentResult(final_report=f"{ticker} memo", state=state)


def test_score_ordering():
    assert _score("bullish", "high") > _score("bullish", "low")
    assert _score("bullish", "low") > _score("bearish", "high")
    assert _score("neutral", "medium") == 2


def test_compare_ranks_by_verdict_and_conviction():
    agent = _FakeAgent({
        "NVDA": ("bullish", "high", 900.0),
        "AMD": ("neutral", "medium", 160.0),
        "INTC": ("bearish", "high", 30.0),
    })

    result = compare_stocks("AI chip competition", ["nvda", "amd", "intc"], agent=agent)

    assert [s.ticker for s in result.stocks] == ["NVDA", "AMD", "INTC"]
    assert result.top_pick == "NVDA"
    assert result.stocks[0].price == 900.0
    assert result.stocks[0].valuation_view == "NVDA view"
    assert "Top pick: NVDA" in result.summary
    assert "Not investment advice" in result.summary


def test_compare_requires_tickers():
    import pytest

    with pytest.raises(ValueError):
        compare_stocks("q", [], agent=_FakeAgent({}))
