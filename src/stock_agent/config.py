from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration for the agent.

    LLM provider is auto-detected from environment variables with priority:
    miromind (竞赛主办方模型) > deepseek > openai. All three speak the
    OpenAI-compatible chat-completions API, so they share one client path.
    """

    # ── LLM provider ──
    provider: str = "miromind"
    model: str = "mirothinker-1-7-deepresearch-mini"
    base_url: Optional[str] = "https://api.miromind.ai/v1"
    api_key: Optional[str] = None
    # Whether the provider reliably supports OpenAI-style structured output
    # (function calling). miromind/deepseek default to False -> manual JSON
    # parsing path; OpenAI defaults to True.
    use_structured_output: bool = False
    temperature: float = 0.2

    # ── pipeline tuning ──
    max_iterations: int = 2
    max_results_per_query: int = 5
    timeout_s: int = 25

    @staticmethod
    def from_env() -> "AgentConfig":
        miromind_key = os.getenv("MIROMIND_API_KEY")
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        if miromind_key:
            provider = "miromind"
            model = os.getenv("MIROMIND_MODEL", "mirothinker-1-7-deepresearch-mini")
            base_url = os.getenv("MIROMIND_BASE_URL", "https://api.miromind.ai/v1")
            api_key = miromind_key
            use_structured = _env_bool("MIROMIND_STRUCTURED_OUTPUT", False)
        elif deepseek_key:
            provider = "deepseek"
            model = os.getenv("DEEPSEEK_MODEL", os.getenv("OPENAI_MODEL", "deepseek-chat"))
            base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
            api_key = deepseek_key
            use_structured = _env_bool("DEEPSEEK_STRUCTURED_OUTPUT", False)
        else:
            provider = "openai"
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            base_url = os.getenv("OPENAI_BASE_URL") or None
            api_key = openai_key
            use_structured = _env_bool("OPENAI_STRUCTURED_OUTPUT", True)

        return AgentConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            use_structured_output=use_structured,
            temperature=float(os.getenv("STOCK_AGENT_TEMPERATURE", "0.2")),
            max_iterations=int(os.getenv("STOCK_AGENT_MAX_ITERATIONS", "2")),
            max_results_per_query=int(os.getenv("STOCK_AGENT_MAX_RESULTS", "5")),
            timeout_s=int(os.getenv("STOCK_AGENT_TIMEOUT_S", "25")),
        )
