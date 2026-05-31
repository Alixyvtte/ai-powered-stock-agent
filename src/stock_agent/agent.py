from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

from dotenv import load_dotenv

from .config import AgentConfig
from .event_adapter import (
    WorkbenchEvent,
    build_report_delta_event,
    build_run_completed_event,
    build_run_failed_event,
    build_run_started_event,
    build_step_event,
)
from .graphs.deep_search_graph import build_deep_search_graph


@dataclass(frozen=True)
class AgentResult:
    final_report: str
    state: Dict[str, Any]


class DeepSearchAgent:
    def __init__(self, config: Optional[AgentConfig] = None):
        load_dotenv()
        self._config = config or AgentConfig.from_env()
        self._graph = build_deep_search_graph(self._config)

    @property
    def config(self) -> AgentConfig:
        return self._config

    def run(self, query: str) -> AgentResult:
        state: Dict[str, Any] = {
            "query": query,
            "max_iterations": self._config.max_iterations,
        }
        out = self._graph.invoke(state)
        report = str(out.get("final_report") or "")
        return AgentResult(final_report=report, state=dict(out))

    def stream(self, query: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
        state: Dict[str, Any] = {
            "query": query,
            "max_iterations": self._config.max_iterations,
        }
        for ev in self._graph.stream(state, stream_mode="updates"):
            if isinstance(ev, dict) and len(ev) == 1:
                node, update = next(iter(ev.items()))
                if isinstance(update, dict):
                    yield node, update
                else:
                    yield node, {"_raw": update}
            else:
                yield "event", {"_raw": ev}

    def stream_events(self, query: str) -> Iterator[WorkbenchEvent]:
        snapshot: Dict[str, Any] = {
            "query": query,
            "max_iterations": self._config.max_iterations,
        }
        last_node: str | None = None

        yield build_run_started_event(query, snapshot)
        try:
            if self._config.stream_tokens:
                # Combined stream: node "updates" drive step events, while
                # "messages" surface the final report's tokens live. The report
                # streams as it is written instead of arriving all at once.
                state: Dict[str, Any] = {
                    "query": query,
                    "max_iterations": self._config.max_iterations,
                }
                for mode, data in self._graph.stream(state, stream_mode=["updates", "messages"]):
                    if mode == "messages":
                        delta = self._report_delta_from_message(data)
                        if delta:
                            yield build_report_delta_event(delta)
                        continue
                    if not isinstance(data, dict):
                        continue
                    for node, update in data.items():
                        if not isinstance(update, dict):
                            continue
                        event = build_step_event(node, snapshot, update)
                        if event is None:
                            continue
                        snapshot = dict(event["snapshot"])
                        last_node = node
                        yield event
            else:
                for node, update in self.stream(query):
                    if not isinstance(update, dict):
                        continue
                    event = build_step_event(node, snapshot, update)
                    if event is None:
                        continue
                    snapshot = dict(event["snapshot"])
                    last_node = node
                    yield event
        except Exception as exc:
            yield build_run_failed_event(str(exc), snapshot, node=last_node)
            return

        yield build_run_completed_event(snapshot)

    @staticmethod
    def _report_delta_from_message(data: Any) -> str:
        """Extract write_report token text from a LangGraph messages-mode item.

        messages mode yields ``(message_chunk, metadata)``; only tokens emitted
        inside the ``write_report`` node are surfaced as report deltas.
        """
        try:
            chunk, meta = data
        except (TypeError, ValueError):
            return ""
        if not isinstance(meta, dict) or meta.get("langgraph_node") != "write_report":
            return ""
        content = getattr(chunk, "content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return content or ""
