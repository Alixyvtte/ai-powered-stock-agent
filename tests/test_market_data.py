from __future__ import annotations

from stock_agent.tools.market_data import _normalize_dividend_yield


def test_normalize_dividend_yield():
    assert _normalize_dividend_yield(None) is None
    assert _normalize_dividend_yield(0.025) == 2.5     # fraction -> percent
    assert _normalize_dividend_yield(2.5) == 2.5       # already a percent
    assert _normalize_dividend_yield(250.0) == 2.5     # double-scaled -> percent
    assert _normalize_dividend_yield(0) == 0
