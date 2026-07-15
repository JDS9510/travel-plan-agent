"""
大模型通用封装层 —— 基于 langchain_openai.ChatOpenAI，支持多模型分层。

完全兼容 LangChain / LangGraph 生态，支持：
- 自动加载项目根目录 .env 环境变量
- 工具调用（tool calling）
- 节点编排（LangGraph node）
- 流式与非流式对话
- 主模型 + 低成本小模型 双通道，自动降级

环境变量配置:
    LLM_API_KEY       主模型 API Key（必填）
    LLM_BASE_URL      主模型 Base URL
    LLM_MODEL         主模型名称（默认 deepseek-chat）

    LLM_SMALL_API_KEY  小模型 API Key（可选，缺失时复用主模型）
    LLM_SMALL_BASE_URL 小模型 Base URL（可选，缺失时复用主模型）
    LLM_SMALL_MODEL    小模型名称（可选，默认同主模型）
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# 自动加载项目根目录 .env
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


class LLMClient:
    """基于 ChatOpenAI 的大模型客户端封装，支持主/小双模型通道。

    自动从环境变量读取配置，也支持实例化时手工覆盖。
    原生兼容 LangGraph 工具调用与节点编排。

    双模型通道：
    - 主模型（_chat_openai）: 用于复杂推理任务（行程生成、ReAct 推理）
    - 小模型（_small_chat_openai）: 用于简单任务（需求解析、格式校验、结果汇总）
    - 小模型未配置时自动复用主模型；小模型调用失败时自动降级到主模型
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        # 小模型配置
        small_api_key: Optional[str] = None,
        small_base_url: Optional[str] = None,
        small_model: Optional[str] = None,
    ) -> None:
        # ---- 主模型配置 ----
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.temperature = temperature
        self.max_tokens = max_tokens

        if not self.api_key:
            raise ValueError(
                "LLM_API_KEY 未设置。请在 .env 文件中配置或通过参数传入。"
            )

        # 构造底层 ChatOpenAI 实例 —— 原生兼容 LangGraph
        self._chat_openai = ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        # ---- 小模型配置（降级策略：未配置则复用主模型） ----
        self.small_api_key = small_api_key or os.getenv("LLM_SMALL_API_KEY", "") or self.api_key
        self.small_base_url = small_base_url or os.getenv("LLM_SMALL_BASE_URL", "") or self.base_url
        self.small_model = small_model or os.getenv("LLM_SMALL_MODEL", "") or self.model

        self._has_small_model = bool(
            os.getenv("LLM_SMALL_MODEL", "") or small_model
        )

        self._small_chat_openai = ChatOpenAI(
            api_key=self.small_api_key,
            base_url=self.small_base_url,
            model=self.small_model,
            temperature=min(temperature, 0.3),  # 小模型用更低温度保证稳定性
            max_tokens=min(max_tokens, 2048),
        )

        # ---- 调用统计 ----
        self._main_count: int = 0
        self._small_count: int = 0
        self._small_fallback_count: int = 0

    # ----------------------------------------------------------
    # 公开方法
    # ----------------------------------------------------------
    def chat(self, prompt: str, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        """使用主模型发送纯文本对话。

        Args:
            prompt: 用户输入文本。
            temperature: 可选温度参数，覆盖实例默认值。
            max_tokens: 可选最大输出 tokens 限制。

        Returns:
            str: 大模型返回的文本内容。

        Raises:
            ConnectionError: API 网络连接异常。
            ValueError: API 返回异常或参数错误。
        """
        if max_tokens is not None:
            original_max_tokens = self._chat_openai.max_tokens
            self._chat_openai.max_tokens = max_tokens
            try:
                return self._invoke_with_retry(self._chat_openai, prompt, temperature)
            finally:
                self._chat_openai.max_tokens = original_max_tokens
        return self._invoke_with_retry(self._chat_openai, prompt, temperature)

    def chat_small(
        self,
        prompt: str,
        temperature: Optional[float] = None,
    ) -> str:
        """使用小模型发送纯文本对话，失败时自动降级到主模型。

        Args:
            prompt: 用户输入文本。
            temperature: 可选温度参数。

        Returns:
            str: 模型返回的文本内容。
        """
        try:
            result = self._invoke_with_retry(
                self._small_chat_openai, prompt, temperature,
            )
            self._small_count += 1
            return result
        except Exception:
            # 降级到主模型
            self._small_fallback_count += 1
            self._main_count += 1
            return self._invoke_with_retry(self._chat_openai, prompt, temperature)

    def get_chat_openai(self, small: bool = False) -> ChatOpenAI:
        """返回底层 ChatOpenAI 实例，供 LangGraph 节点/工具直接使用。

        Args:
            small: True 返回小模型实例，False 返回主模型实例。
        """
        if small:
            return self._small_chat_openai
        return self._chat_openai

    def get_chat_openai_with_fallback(self, small: bool = False) -> ChatOpenAI:
        """返回底层 ChatOpenAI 实例，小模型失败时自动回退主模型。

        此方法供 LangGraph bind_tools 场景使用，
        小模型可能不支持 tool calling，此时返回主模型。

        Args:
            small: True 优先使用小模型。

        Returns:
            ChatOpenAI: 底层实例。
        """
        if not small or not self._has_small_model:
            return self._chat_openai
        # 小模型已显式配置才返回，否则直接主模型
        return self._small_chat_openai

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------
    @property
    def stats(self) -> dict[str, Any]:
        """返回模型调用统计信息，用于成本分析。"""
        return {
            "main_model": self.model,
            "small_model": self.small_model if self._has_small_model else "(复用主模型)",
            "main_calls": self._main_count,
            "small_calls": self._small_count,
            "small_fallbacks": self._small_fallback_count,
            "total_calls": self._main_count + self._small_count,
        }

    # ----------------------------------------------------------
    # 内部
    # ----------------------------------------------------------
    @staticmethod
    def _invoke_with_retry(
        chat_openai: ChatOpenAI,
        prompt: str,
        temperature: Optional[float] = None,
    ) -> str:
        """调用 ChatOpenAI 发送文本，含真正 2 次重试 + 指数退避。

        重试策略：
        - 第 1 次尝试失败后等待 1.0s 重试
        - 第 2 次失败后等待 3.0s 重试
        - 3 次全部失败后分类抛出异常

        每次调用均记录追踪日志（成功/失败）。
        """
        import logging
        _logger = logging.getLogger(__name__)

        if temperature is not None:
            chat_openai.temperature = temperature

        last_error: Optional[str] = None
        max_attempts = 3  # 1 initial + 2 retries

        for attempt in range(max_attempts):
            start_ts = time.time()
            try:
                response = chat_openai.invoke(prompt)
                content = response.content if hasattr(response, "content") else str(response)

                # 追踪成功调用
                _trace_llm_call(prompt, content, start_ts)
                if attempt > 0:
                    _logger.info(
                        "LLM 重试成功 | attempt=%d/%d | latency_ms=%d",
                        attempt + 1, max_attempts,
                        int((time.time() - start_ts) * 1000),
                    )
                return content.strip()

            except Exception as exc:
                last_error = str(exc)
                _trace_llm_call(prompt, f"ERROR: {last_error}", start_ts, last_error)

                if attempt < max_attempts - 1:
                    wait_s = 1.0 * (1.5 ** attempt)  # 1.0s → 3.0s
                    _logger.warning(
                        "LLM 调用失败，准备重试 | attempt=%d/%d | wait=%.1fs | error=%s",
                        attempt + 1, max_attempts, wait_s, last_error[:120],
                    )
                    time.sleep(wait_s)
                    continue

        # 所有重试耗尽
        _logger.error(
            "LLM 调用全部重试失败 | attempts=%d | last_error=%s",
            max_attempts, (last_error or "未知错误")[:200],
        )
        if last_error and ("timeout" in last_error.lower() or "connection" in last_error.lower()):
            raise ConnectionError(
                f"大模型 API 网络连接失败（已重试 {max_attempts} 次）: {last_error}"
            )
        raise ValueError(
            f"大模型 API 调用失败（已重试 {max_attempts} 次）: {last_error}"
        )


def _trace_llm_call(
    prompt: str,
    response: str,
    start_ts: float,
    error: Optional[str] = None,
) -> None:
    """记录 LLM 调用到追踪系统。"""
    try:
        from src.utils.tracer import get_tracer
        duration_ms = round((time.time() - start_ts) * 1000, 2)
        get_tracer().trace_llm_call(
            prompt_preview=prompt[:200],
            response_preview=response[:200],
            duration_ms=duration_ms,
            error=error,
        )
    except Exception:
        pass


# ============================================================
# 全局单例
# ============================================================
_llm_client_instance: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端实例（延迟初始化）。"""
    global _llm_client_instance
    if _llm_client_instance is None:
        _llm_client_instance = LLMClient()
    return _llm_client_instance


# 兼容旧代码的别名：模块级属性，首次访问时自动初始化
class _LazyLLMClient:
    """延迟初始化代理 —— 首次调用属性时才初始化。"""

    def __getattr__(self, name: str):
        return getattr(get_llm_client(), name)


llm_client: Any = _LazyLLMClient()  # type: ignore[assignment]
