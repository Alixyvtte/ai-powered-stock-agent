from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

from dotenv import load_dotenv

from .config import AgentConfig
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
