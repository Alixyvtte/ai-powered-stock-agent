from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    currency: Optional[str]
    price: Optional[float]
    market_cap: Optional[float]
    trailing_pe: Optional[float]
    forward_pe: Optional[float]
    dividend_yield: Optional[float]


def fetch_market_snapshot(ticker: str) -> MarketSnapshot:
    import yfinance as yf

    t = yf.Ticker(ticker)
    info: Dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception:
        info = {}

    def f(key: str) -> Optional[float]:
        v = info.get(key)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    return MarketSnapshot(
        ticker=ticker,
        currency=(info.get("currency") or None),
        price=f("currentPrice") or f("regularMarketPrice"),
        market_cap=f("marketCap"),
        trailing_pe=f("trailingPE"),
        forward_pe=f("forwardPE"),
        dividend_yield=f("dividendYield"),
    )

