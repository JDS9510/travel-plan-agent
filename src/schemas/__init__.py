"""
数据模型包 —— 统一导出所有 Pydantic v2 行程数据结构。

使用方式:
    from src.schemas import Spot, DailyPlan, TravelPlan, UserDemand
    from src.schemas import DemandAnalyzeOutput, OutlineOutput, CheckOutput
"""

from __future__ import annotations

from src.schemas.models import (
    CheckOutput,
    DailyFramework,
    DailyPlan,
    DemandAnalyzeOutput,
    OutlineOutput,
    Spot,
    TravelPlan,
    UserDemand,
)

__all__ = [
    "Spot",
    "DailyPlan",
    "TravelPlan",
    "UserDemand",
    "DemandAnalyzeOutput",
    "DailyFramework",
    "OutlineOutput",
    "CheckOutput",
]
