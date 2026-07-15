"""
模型任务路由 —— 根据任务复杂度自动分配主模型或小模型。

路由规则：
- 简单任务（低成本小模型）:
    demand_analyze   — 需求拆解（结构化提取）
    plan_check       — 行程校验结果生成（格式固定）
    result_summary   — 结果汇总（模板化输出）
    revise_intent    — 修订意图解析（关键词匹配为主，LLM 为辅）

- 复杂任务（主模型）:
    outline_generate — 行程框架生成（需要创造力）
    daily_fill       — 每日行程填充（核心生成任务）
    react_revise     — ReAct 自主推理修正（需要复杂推理 + 工具调用）

- 降级策略：
    小模型调用失败时自动回退主模型，保证可用性不受影响。

使用方式：
    from src.llm.model_router import ModelRouter, TaskComplexity

    router = ModelRouter()
    response = router.route("demand_analyze", prompt)
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_openai import ChatOpenAI

from src.llm.llm_client import get_llm_client


# ---------------------------------------------------------------
# 任务复杂度枚举
# ---------------------------------------------------------------
class TaskComplexity:
    """任务复杂度常量。"""
    SIMPLE = "simple"     # 低成本小模型
    COMPLEX = "complex"   # 主模型


# 任务 → 复杂度 映射
_TASK_COMPLEXITY: dict[str, str] = {
    "demand_analyze":   TaskComplexity.SIMPLE,
    "fact_check":       TaskComplexity.SIMPLE,
    "plan_check":       TaskComplexity.SIMPLE,
    "result_summary":   TaskComplexity.SIMPLE,
    "revise_intent":    TaskComplexity.SIMPLE,
    "outline_generate": TaskComplexity.COMPLEX,
    "daily_fill":       TaskComplexity.COMPLEX,
    "react_revise":     TaskComplexity.COMPLEX,
}


class ModelRouter:
    """任务驱动的模型路由器。

    根据任务名称自动选择主模型或低成本小模型，
    小模型失败时自动降级到主模型。

    使用方式：
        router = ModelRouter()
        text = router.route("demand_analyze", prompt)
        chat_openai = router.get_chat_openai("daily_fill")
    """

    def __init__(self) -> None:
        self.__client: Any = None  # 延迟初始化，避免启动时因无 API Key 而报错

    def _get_client(self) -> Any:
        """延迟获取 LLM 客户端。"""
        if self.__client is None:
            self.__client = get_llm_client()
        return self.__client

    # ---- 路由方法 ----

    def get_complexity(self, task_name: str) -> str:
        """获取指定任务的复杂度级别。

        Args:
            task_name: 任务名称（如 "demand_analyze"）。

        Returns:
            str: "simple" 或 "complex"。
        """
        return _TASK_COMPLEXITY.get(task_name, TaskComplexity.COMPLEX)

    def route(
        self,
        task_name: str,
        prompt: str,
        temperature: Optional[float] = None,
    ) -> str:
        """根据任务复杂度路由到对应模型执行对话。

        Args:
            task_name: 任务名称。
            prompt: 用户输入文本。
            temperature: 可选温度参数。

        Returns:
            str: 模型返回的文本内容。
        """
        complexity = self.get_complexity(task_name)

        if complexity == TaskComplexity.SIMPLE:
            return self._get_client().chat_small(prompt, temperature)
        else:
            return self._get_client().chat(prompt, temperature)

    def get_chat_openai(self, task_name: str) -> ChatOpenAI:
        """返回适用于指定任务的 ChatOpenAI 实例（用于 bind_tools 等场景）。

        Args:
            task_name: 任务名称。

        Returns:
            ChatOpenAI: 底层实例。
        """
        complexity = self.get_complexity(task_name)

        if complexity == TaskComplexity.SIMPLE:
            return self._get_client().get_chat_openai_with_fallback(small=True)
        else:
            return self._get_client().get_chat_openai(small=False)

    @property
    def stats(self) -> dict[str, Any]:
        """返回模型调用统计信息。"""
        if self.__client is None:
            return {"status": "not_initialized"}
        return self.__client.stats

    @staticmethod
    def is_simple(task_name: str) -> bool:
        """便捷方法：检查任务是否为简单任务。"""
        return _TASK_COMPLEXITY.get(task_name, TaskComplexity.COMPLEX) == TaskComplexity.SIMPLE


# 全局单例
_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    """获取全局 ModelRouter 实例。"""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


# ============================================================
# RoutedLLMAdapter —— 将 ModelRouter 适配为 .chat() 接口
# ============================================================
class RoutedLLMAdapter:
    """将 ModelRouter 适配为 LLMOutputValidator 所需的 llm_client 接口。

    使用方式:
        router = get_model_router()
        adapter = RoutedLLMAdapter("demand_analyze", router)
        text = adapter.chat(prompt, temperature=0.2)
    """

    def __init__(self, task_name: str, router: ModelRouter) -> None:
        self._task_name: str = task_name
        self._router: ModelRouter = router

    def chat(self, prompt: str, temperature: Any = None) -> str:
        return self._router.route(self._task_name, prompt, temperature)
