"""
旅行行程规划项目 —— AI 驱动的智能行程生成系统。

包结构:
    src/
    ├── agent/      LangGraph Agent 核心（state / nodes / graph / react_revise）
    ├── api/        FastAPI 接口层（同步 / 异步 / 缓存）
    ├── llm/        大模型客户端封装（OpenAI-compatible）
    ├── rag/        向量检索知识库（Chroma + BGE）
    ├── schemas/    Pydantic v2 数据模型
    ├── services/   业务服务层（任务管理 / 缓存）
    ├── tools/      LangChain 工具集（景点 / 预算 / 校验 / 天气）
    └── utils/      工具包（校验器 / 追踪器）

使用方式:
    from src.agent import run_travel_planner
    result = run_travel_planner({...})

    # 或启动 API 服务:
    # uvicorn src.api:app --reload
"""

from __future__ import annotations

__all__: list[str] = []
