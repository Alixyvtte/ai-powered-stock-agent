from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Speed/quality presets. Each bundles the knobs that trade coverage depth for
# latency. Individual env vars still override the chosen preset.
PRESETS: dict[str, dict[str, int]] = {
    "fast": {
        "max_iterations": 1,
        "max_results_per_query": 5,
        "timeout_s": 12,
        "extract_batch_size": 5,
        "fetch_top_n": 0,        # snippets only — fastest
    },
    "standard": {
        "max_iterations": 2,
        "max_results_per_query": 5,
        "timeout_s": 20,
        "extract_batch_size": 8,
        "fetch_top_n": 3,        # fetch full text for the 3 best sources
    },
    "deep": {
        "max_iterations": 3,
        "max_results_per_query": 8,
        "timeout_s": 30,
        "extract_batch_size": 12,
        "fetch_top_n": 6,
    },
}
DEFAULT_MODE = "standard"


def normalize_mode(mode: Optional[str]) -> str:
    candidate = (mode or "").strip().lower()
    return candidate if candidate in PRESETS else DEFAULT_MODE


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
    mode: str = DEFAULT_MODE
    max_iterations: int = 2
    max_results_per_query: int = 5
    timeout_s: int = 20
    # Max sources extracted per pass + concurrency of their (parallel) LLM calls.
    extract_batch_size: int = 8
    extract_max_workers: int = 8
    # How many top sources to fetch full readable text for (0 = snippets only).
    # Dataclass default is 0 so a bare AgentConfig() never makes network fetches
    # (e.g. in tests); from_env applies the preset value (standard=3, deep=6).
    fetch_top_n: int = 0

    # ── caching (search / page content / market snapshots) ──
    enable_cache: bool = True
    cache_dir: Optional[str] = None

    # ── streaming ──
    # Stream the final report token-by-token to the web UI (perceived speed).
    # Requires the provider to support server-side streaming; disable if a
    # provider rejects `stream=true` (verify via scripts/probe_miromind.py).
    stream_tokens: bool = True

    @staticmethod
    def from_env(mode: Optional[str] = None) -> "AgentConfig":
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

        resolved_mode = normalize_mode(mode or os.getenv("STOCK_AGENT_MODE", DEFAULT_MODE))
        preset = PRESETS[resolved_mode]

        return AgentConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            use_structured_output=use_structured,
            temperature=float(os.getenv("STOCK_AGENT_TEMPERATURE", "0.2")),
            mode=resolved_mode,
            max_iterations=int(os.getenv("STOCK_AGENT_MAX_ITERATIONS", str(preset["max_iterations"]))),
            max_results_per_query=int(os.getenv("STOCK_AGENT_MAX_RESULTS", str(preset["max_results_per_query"]))),
            timeout_s=int(os.getenv("STOCK_AGENT_TIMEOUT_S", str(preset["timeout_s"]))),
            extract_batch_size=int(os.getenv("STOCK_AGENT_EXTRACT_BATCH", str(preset["extract_batch_size"]))),
            extract_max_workers=int(os.getenv("STOCK_AGENT_EXTRACT_WORKERS", "8")),
            fetch_top_n=int(os.getenv("STOCK_AGENT_FETCH_TOP_N", str(preset["fetch_top_n"]))),
            enable_cache=_env_bool("STOCK_AGENT_CACHE", True),
            cache_dir=os.getenv("STOCK_AGENT_CACHE_DIR") or None,
            stream_tokens=_env_bool("STOCK_AGENT_STREAM", True),
        )
