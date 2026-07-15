"""
自定义工具集 —— 统一导出所有 LangChain @tool 工具函数。

使用方式:
    from src.tools import (
        spot_retriever,
        budget_calculator,
        plan_checker,
        weather_query,
    )

所有工具基于 langchain @tool 装饰器实现，100% 兼容 LangGraph 工具调用，
可直接绑定到 ChatOpenAI 或作为 StateGraph 节点的可调用工具。
"""

from src.tools.spot_retriever import spot_retriever
from src.tools.budget_calculator import budget_calculator
from src.tools.plan_checker import plan_checker
from src.tools.weather_query import weather_query

__all__ = [
    "spot_retriever",
    "budget_calculator",
    "plan_checker",
    "weather_query",
]
