from __future__ import annotations

from typing import Optional

from langchain_openai import ChatOpenAI

from .config import AgentConfig


def get_chat_model(config: AgentConfig, *, temperature: Optional[float] = None) -> ChatOpenAI:
    """Build an OpenAI-compatible chat model from config.

    miromind / deepseek / openai all expose the same chat-completions API,
    so they only differ by ``base_url`` / ``model`` / ``api_key``.

    ``temperature`` can be overridden per call site (e.g. lower for planning,
    higher for prose) without rebuilding the whole config.
    """
    if not config.api_key:
        raise RuntimeError(
            f"缺少 API key（provider={config.provider}）。"
            "请在 .env 中设置 MIROMIND_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY 之一。"
        )

    kwargs = {
        "model": config.model,
        "api_key": config.api_key,
        "temperature": config.temperature if temperature is None else temperature,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return ChatOpenAI(**kwargs)
