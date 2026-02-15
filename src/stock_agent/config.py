from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AgentConfig:
    openai_model: str = "gpt-4o-mini"
    max_iterations: int = 2
    max_results_per_query: int = 5
    timeout_s: int = 25

    @staticmethod
    def from_env() -> "AgentConfig":
        return AgentConfig(
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            max_iterations=int(os.getenv("STOCK_AGENT_MAX_ITERATIONS", "2")),
            max_results_per_query=int(os.getenv("STOCK_AGENT_MAX_RESULTS", "5")),
            timeout_s=int(os.getenv("STOCK_AGENT_TIMEOUT_S", "25")),
        )

