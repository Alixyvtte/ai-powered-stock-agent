from __future__ import annotations

from stock_agent.web.presentation import (
    build_market_highlights,
    collect_followup_history,
    render_final_report_html,
)


def test_render_final_report_html_sanitizes_output() -> None:
    html = render_final_report_html("# Final Memo\n\n<script>alert('x')</script>\n\nUse **caution**.")

    assert html is not None
    assert "<h1>Final Memo</h1>" in html
    assert "<strong>caution</strong>" in html
    assert "<script>" not in html
    assert "alert" in html


def test_collect_followup_history_preserves_first_seen_unique_queries() -> None:
    history = collect_followup_history(
        [
            {
                "type": "step_completed",
                "node": "decide",
                "snapshot": {"followup_queries": ["NVDA supply chain", "NVDA valuation"]},
            },
            {
                "type": "step_completed",
                "node": "search_web",
                "snapshot": {"followup_queries": ["ignored non-decide event"]},
            },
            {
                "type": "step_completed",
                "node": "decide",
                "snapshot": {"followup_queries": ["NVDA valuation", "NVDA export controls"]},
            },
            {
                "type": "step_completed",
                "node": "decide",
                "snapshot": {"followup_queries": []},
            },
        ]
    )

    assert history == [
        "NVDA supply chain",
        "NVDA valuation",
        "NVDA export controls",
    ]


def test_build_market_highlights_normalizes_first_successful_provider_payload() -> None:
    highlights = build_market_highlights(
        {
            "market": {
                "NVDA": {
                    "yfinance": {"ticker": "NVDA", "error": "timeout"},
                    "manual": {
                        "ticker": "NVDA",
                        "currency": "USD",
                        "price": 910.25,
                        "market_cap": 2220000000000,
                        "forward_pe": 31.2,
                    },
                },
                "BABA": {
                    "akshare": {
                        "ticker": "BABA",
                        "currency": "CNY",
                        "price": 88.1,
                        "market_cap_cny": 1600000000000,
                        "trailing_pe": 14.8,
                        "dividend_yield": 0.012,
                    }
                },
            }
        }
    )

    assert [highlight.ticker for highlight in highlights] == ["NVDA", "BABA"]
    assert highlights[0].source == "manual"
    assert highlights[0].market_cap == 2220000000000
    assert highlights[1].source == "akshare"
    assert highlights[1].market_cap == 1600000000000
    assert highlights[1].dividend_yield == 0.012
