from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from .config import AgentConfig


def get_chat_model(config: AgentConfig) -> ChatOpenAI:
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        model = os.getenv("DEEPSEEK_MODEL", config.openai_model)
        return ChatOpenAI(
            model=model,
            api_key=deepseek_key,
            base_url=base_url,
            temperature=0.2,
        )

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")

    return ChatOpenAI(model=config.openai_model, api_key=openai_key, temperature=0.2)
