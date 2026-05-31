from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
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
    week_52_high: Optional[float]
    week_52_low: Optional[float]
    beta: Optional[float]
    analyst_recommendation: Optional[str]
    revenue_growth: Optional[float]
    retrieved_at: str = field(default="")
    source: str = field(default="yfinance")


@dataclass(frozen=True)
class AShareSnapshot:
    ticker: str
    name: Optional[str]
    industry: Optional[str]
    price: Optional[float]
    market_cap_cny: Optional[str]   # raw string from akshare, e.g. "5000亿"
    pe_ratio: Optional[float]       # 市盈率(动)
    pb_ratio: Optional[float]       # 市净率
    eps: Optional[float]            # 每股收益
    retrieved_at: str = field(default="")
    source: str = field(default="akshare")


def _normalize_dividend_yield(raw: Optional[float]) -> Optional[float]:
    """Normalize yfinance's inconsistent dividend yield to a percent.

    Across yfinance versions the field is sometimes a fraction (0.025) and
    sometimes already a percent (2.5). Values < 1 are treated as fractions and
    scaled up; obviously double-scaled values (> 100) are scaled back. Result is
    a percent rounded to 2 dp (e.g. 2.5 means 2.5%).
    """
    if raw is None:
        return None
    value = raw * 100 if raw <= 1 else raw
    if value > 100:
        value = value / 100
    return round(value, 2)


def _normalize_ticker_for_yfinance(ticker: str) -> str:
    """Append exchange suffix for A-share 6-digit codes so yfinance can resolve them."""
    import re
    if re.fullmatch(r"\d{6}", ticker):
        if ticker.startswith(("60", "68")):
            return ticker + ".SS"   # Shanghai Stock Exchange
        if ticker.startswith(("00", "30")):
            return ticker + ".SZ"   # Shenzhen Stock Exchange
    return ticker


def fetch_market_snapshot(ticker: str) -> MarketSnapshot:
    import yfinance as yf

    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
    yf_ticker = _normalize_ticker_for_yfinance(ticker)
    t = yf.Ticker(yf_ticker)
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
        dividend_yield=_normalize_dividend_yield(f("dividendYield")),
        week_52_high=f("fiftyTwoWeekHigh"),
        week_52_low=f("fiftyTwoWeekLow"),
        beta=f("beta"),
        analyst_recommendation=(info.get("recommendationKey") or None),
        revenue_growth=f("revenueGrowth"),
        retrieved_at=retrieved_at,
        source="yfinance",
    )


def fetch_a_share_snapshot(ticker: str) -> AShareSnapshot:
    import akshare as ak

    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
    # Normalize: strip common suffixes (.SS .SZ .SH) so akshare gets a 6-digit code
    clean = ticker.upper().replace(".SS", "").replace(".SZ", "").replace(".SH", "")

    info: Dict[str, Any] = {}
    try:
        df = ak.stock_individual_info_em(symbol=clean)
        if df is not None and not df.empty:
            info = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
    except Exception:
        pass

    def f(key: str) -> Optional[float]:
        v = info.get(key)
        if v is None:
            return None
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return None

    return AShareSnapshot(
        ticker=clean,
        name=(str(info.get("股票简称", "")) or None),
        industry=(str(info.get("行业", "")) or None),
        price=f("最新价"),
        market_cap_cny=(str(info.get("总市值", "")) or None),
        pe_ratio=f("市盈率(动)"),
        pb_ratio=f("市净率"),
        eps=f("每股收益"),
        retrieved_at=retrieved_at,
        source="akshare",
    )
