"""
API 包 —— 对外暴露 FastAPI app 实例与数据模型。

使用方式:
    from src.api import app
    from src.api.schemas import TravelPlanRequest, ApiResponse, ReviseRequest

    # 或直接启动:
    # uvicorn src.api.main:app --reload
    # uvicorn src.api:app --reload
"""

from __future__ import annotations

from src.api.main import app
from src.api.schemas import (
    ApiResponse,
    AsyncTaskResponse,
    ExportRequest,
    ReviseRequest,
    TaskStatusResponse,
    TravelPlanRequest,
)

__all__ = [
    "app",
    "TravelPlanRequest",
    "ApiResponse",
    "AsyncTaskResponse",
    "TaskStatusResponse",
    "ReviseRequest",
    "ExportRequest",
]
