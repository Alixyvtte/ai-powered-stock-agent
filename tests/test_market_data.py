from __future__ import annotations

from stock_agent.tools.market_data import _normalize_dividend_yield


def test_normalize_dividend_yield():
    assert _normalize_dividend_yield(None) is None
    assert _normalize_dividend_yield(0.025) == 2.5     # fraction -> percent
    assert _normalize_dividend_yield(2.5) == 2.5       # already a percent
    assert _normalize_dividend_yield(250.0) == 2.5     # double-scaled -> percent
    assert _normalize_dividend_yield(0) == 0


def test_fetch_market_snapshot_maps_enriched_fields(monkeypatch):
    """fetch_market_snapshot parses the enriched fundamentals from .info (offline)."""
    import yfinance

    class _FakeTicker:
        def __init__(self, *a, **k):
            pass

        @property
        def info(self):
            return {
                "currency": "USD", "currentPrice": 900.0, "marketCap": 2.2e12,
                "trailingPE": 45.0, "forwardPE": 30.0, "dividendYield": 0.0008,
                "fiftyTwoWeekHigh": 950.0, "fiftyTwoWeekLow": 400.0, "beta": 1.7,
                "recommendationKey": "buy", "revenueGrowth": 0.22,
                "shortName": "NVIDIA Corp", "sector": "Technology", "industry": "Semiconductors",
                "targetMeanPrice": 1050.0, "numberOfAnalystOpinions": 50,
                "profitMargins": 0.48, "grossMargins": 0.75, "returnOnEquity": 0.9,
                "totalRevenue": 60e9, "earningsGrowth": 0.5, "twoHundredDayAverage": 820.0,
            }

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    from stock_agent.tools.market_data import fetch_market_snapshot

    snap = fetch_market_snapshot("NVDA")
    assert snap.name == "NVIDIA Corp"
    assert snap.sector == "Technology"
    assert snap.industry == "Semiconductors"
    assert snap.target_mean_price == 1050.0
    assert snap.num_analyst_opinions == 50
    assert snap.profit_margins == 0.48
    assert snap.gross_margins == 0.75
    assert snap.return_on_equity == 0.9
    assert snap.total_revenue == 60e9
    assert snap.earnings_growth == 0.5
    assert snap.two_hundred_day_average == 820.0
    assert snap.dividend_yield == 0.08   # 0.0008 fraction -> 0.08%
