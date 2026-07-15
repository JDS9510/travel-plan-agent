"""
大模型客户端包 —— 提供 OpenAI-compatible LLM 封装，支持多模型分层路由。

使用方式:
    from src.llm import llm_client, LLMClient, get_llm_client
    from src.llm import ModelRouter, get_model_router

    # 任务路由
    router = get_model_router()
    text = router.route("demand_analyze", prompt)  # 自动用小模型
    text = router.route("daily_fill", prompt)      # 自动用主模型
"""

from __future__ import annotations

from src.llm.llm_client import LLMClient, get_llm_client, llm_client
from src.llm.model_router import (
    ModelRouter,
    RoutedLLMAdapter,
    TaskComplexity,
    get_model_router,
)

__all__ = [
    "LLMClient",
    "get_llm_client",
    "llm_client",
    "ModelRouter",
    "RoutedLLMAdapter",
    "TaskComplexity",
    "get_model_router",
]
