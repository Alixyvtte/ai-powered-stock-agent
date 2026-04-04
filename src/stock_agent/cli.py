from __future__ import annotations

import argparse
import json
import logging
import sys

from rich.console import Console
from rich.panel import Panel

from .agent import DeepSearchAgent
from .event_adapter import collect_step_warnings, summarize_step


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stock-agent")
    p.add_argument("query", help="研究问题，例如：深度研究 NVDA 未来6-12个月催化剂与风险")
    p.add_argument("--json", action="store_true", help="输出完整state为JSON")
    p.add_argument("--no-trace", action="store_true", help="禁用逐步执行输出")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    agent = DeepSearchAgent()
    if not args.no_trace:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    def print_step(node: str, update: dict, state_before: dict, state_after: dict) -> None:
        summary = summarize_step(node, state_before, state_after)
        warnings = collect_step_warnings(node, summary)

        if node == "plan":
            lines = [
                f"topic: {summary['topic']}",
                f"tickers: {summary['ticker_count']}",
                f"subqueries: {summary['subquery_count']}",
            ]
        elif node == "market":
            if summary["ticker_count"] == 0:
                lines = ["no ticker detected"]
            else:
                lines = [
                    f"tickers: {summary['ticker_count']}",
                    f"covered: {summary['covered_ticker_count']}",
                    f"with price: {summary['tickers_with_price']}",
                    f"with market cap: {summary['tickers_with_market_cap']}",
                ]
        elif node == "search_web":
            lines = [
                f"sources: {summary['total_sources']}",
                f"new sources: +{summary['new_sources']}",
            ]
        elif node == "extract":
            lines = [
                f"evidence notes: {summary['total_notes']}",
                f"new notes: +{summary['new_notes']}",
            ]
        elif node == "decide":
            lines = [
                f"need_more: {summary['need_more']}",
                f"followup_queries: {summary['followup_count']}",
                f"evidence_confidence: {summary['evidence_confidence']}",
            ]
        elif node == "write_report":
            lines = [f"final_report chars: {summary['report_length']}"]
        else:
            console.print(Panel(json.dumps(update, ensure_ascii=False, indent=2), title=node, border_style="cyan"))
            return

        if warnings:
            lines.extend(f"warning: {warning['code']}" for warning in warnings)
        console.print(Panel("\n".join(lines), title=node, border_style="cyan"))

    try:
        if args.json or args.no_trace:
            result = agent.run(args.query)
            if args.json:
                console.print(json.dumps(result.state, ensure_ascii=False, indent=2))
            else:
                console.print(Panel(result.final_report, title="Deep Search Report", border_style="green"))
            return 0

        state: dict = {"query": args.query, "max_iterations": agent.config.max_iterations}
        for node, update in agent.stream(args.query):
            before = dict(state)
            if isinstance(update, dict):
                state.update(update)
            print_step(node, update, before, state)
        report = str(state.get("final_report") or "")
        console.print(Panel(report, title="Deep Search Report", border_style="green"))
    except Exception as e:
        console.print(Panel(str(e), title="运行失败", border_style="red"))
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
