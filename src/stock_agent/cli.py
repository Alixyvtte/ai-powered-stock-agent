from __future__ import annotations

import argparse
import json
import logging
import sys

from rich.console import Console
from rich.panel import Panel

from .agent import DeepSearchAgent


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
        if node == "plan":
            topic = ((state_after.get("plan") or {}).get("topic") or "").strip()
            sq = state_after.get("subqueries") or []
            console.print(Panel(f"topic: {topic}\nsubqueries: {len(sq)}", title="plan", border_style="cyan"))
            return
        if node == "market":
            m = state_after.get("market") or {}
            if m:
                console.print(Panel(json.dumps(m, ensure_ascii=False, indent=2), title="market", border_style="cyan"))
            else:
                console.print(Panel("no ticker detected", title="market", border_style="cyan"))
            return
        if node == "search_web":
            prev = len(state_before.get("sources") or [])
            now = len(state_after.get("sources") or [])
            console.print(Panel(f"sources: {now} (+{max(now - prev, 0)})", title="search_web", border_style="cyan"))
            return
        if node == "extract":
            prev = len(state_before.get("notes") or [])
            now = len(state_after.get("notes") or [])
            console.print(Panel(f"evidence notes: {now} (+{max(now - prev, 0)})", title="extract", border_style="cyan"))
            return
        if node == "decide":
            need_more = bool(state_after.get("need_more"))
            followups = state_after.get("followup_queries") or []
            console.print(
                Panel(
                    f"need_more: {need_more}\nfollowup_queries: {len(followups)}",
                    title="decide",
                    border_style="cyan",
                )
            )
            return
        if node == "write_report":
            r = (state_after.get("final_report") or "").strip()
            console.print(Panel(f"final_report chars: {len(r)}", title="write_report", border_style="cyan"))
            return
        console.print(Panel(json.dumps(update, ensure_ascii=False, indent=2), title=node, border_style="cyan"))

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
