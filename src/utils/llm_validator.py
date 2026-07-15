"""
LLM 输出校验与自动纠错器。

功能：
- 接收大模型原始输出 + 目标 Pydantic 模型，自动做格式校验
- 校验失败时提取错误信息，拼接纠错 Prompt 自动修正
- 最多重试 3 次，全部失败时返回明确错误信息
- 完全兼容现有 Agent 节点调用方式，替换原有 _llm_to_json
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 默认最大重试次数
_DEFAULT_MAX_RETRIES = 3


class LLMOutputValidator:
    """LLM 输出校验器 —— 解析 → 校验 → 纠错重试。

    使用方式:
        validator = LLMOutputValidator(max_retries=3)

        validated_dict, error = validator.validate(
            llm_client=llm_client,
            prompt="...",
            target_model=DemandAnalyzeOutput,
        )
    """

    def __init__(self, max_retries: int = _DEFAULT_MAX_RETRIES) -> None:
        """初始化校验器。

        Args:
            max_retries: 校验失败时的最大重试次数，默认 3。
        """
        self.max_retries = max(max_retries, 0)

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------
    def validate(
        self,
        llm_client: Any,
        prompt: str,
        target_model: type[BaseModel],
        initial_temperature: float = 0.3,
    ) -> tuple[dict[str, Any] | None, str]:
        """调用 LLM 并校验输出，失败时自动纠错重试。

        Args:
            llm_client: LLM 客户端实例（需实现 .chat(prompt, temperature) 方法）。
            prompt: 初始 Prompt 文本。
            target_model: 目标 Pydantic 校验模型。
            initial_temperature: 首次调用的 temperature。

        Returns:
            tuple[dict | None, str]:
                - 成功: (validated_dict, "")
                - 失败: (None, error_message)
        """
        last_error: str = ""
        current_prompt: str = prompt
        temperature: float = initial_temperature

        for attempt in range(self.max_retries + 1):
            try:
                # 1) 调用 LLM
                raw_output: str = llm_client.chat(
                    current_prompt,
                    temperature=temperature,
                )

                # 2) 解析为 dict
                parsed: dict[str, Any] = self._parse_to_dict(raw_output)
                if not parsed:
                    last_error = "LLM 输出无法解析为 JSON"
                    current_prompt = self._build_correction_prompt(
                        current_prompt, last_error, attempt
                    )
                    temperature = max(0.1, temperature - 0.1)
                    logger.warning(
                        "LLM 输出解析失败 (attempt %d/%d): %s",
                        attempt + 1, self.max_retries + 1, last_error,
                    )
                    continue

                # 3) Pydantic 校验
                try:
                    validated = target_model.model_validate(parsed)
                    logger.info(
                        "LLM 输出校验通过 (attempt %d/%d)",
                        attempt + 1, self.max_retries + 1,
                    )
                    return validated.model_dump(), ""
                except Exception as validation_error:
                    last_error = str(validation_error)
                    current_prompt = self._build_correction_prompt(
                        current_prompt, last_error, attempt,
                    )
                    temperature = max(0.1, temperature - 0.1)
                    logger.warning(
                        "Pydantic 校验失败 (attempt %d/%d): %s",
                        attempt + 1, self.max_retries + 1, last_error,
                    )
                    continue

            except Exception as exc:
                last_error = f"LLM 调用异常: {str(exc)}"
                if attempt < self.max_retries:
                    current_prompt = self._build_correction_prompt(
                        current_prompt, last_error, attempt,
                    )
                    temperature = max(0.1, temperature - 0.1)
                    logger.error(
                        "LLM 调用失败 (attempt %d/%d): %s",
                        attempt + 1, self.max_retries + 1, last_error,
                    )
                continue

        # 所有重试耗尽
        return None, f"校验失败（已重试 {self.max_retries} 次）: {last_error}"

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------
    @staticmethod
    def _parse_to_dict(raw: str) -> dict[str, Any]:
        """将 LLM 原始输出解析为 dict，带 Markdown 清洗 + 截断修复。"""
        if not raw or not raw.strip():
            return {}

        cleaned: str = raw.strip()

        # 去除 Markdown 代码块标记
        for prefix in ("```json", "```JSON", "```"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # 定位 JSON 起始位置
        brace_start = cleaned.find("{")
        bracket_start = cleaned.find("[")
        if brace_start == -1 and bracket_start == -1:
            return {}
        start = brace_start if bracket_start == -1 else (
            bracket_start if brace_start == -1 else min(brace_start, bracket_start)
        )
        cleaned = cleaned[start:]

        # 补全缺失括号 —— 按嵌套顺序反向闭合
        stack: list[str] = []
        for ch in cleaned:
            if ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch == "}" and stack and stack[-1] == "}":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "]":
                stack.pop()
        cleaned += "".join(reversed(stack))

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 最后尝试：逐字符截断到最后一个合法位置
            for i in range(len(cleaned), 0, -1):
                try:
                    return json.loads(cleaned[:i])
                except json.JSONDecodeError:
                    continue
            return {}

    @staticmethod
    def _build_correction_prompt(
        original_prompt: str,
        error_detail: str,
        attempt: int,
    ) -> str:
        """构造纠错 Prompt，将校验错误反馈给 LLM。"""
        # 避免 Prompt 无限增长：只保留纠正指令
        correction_header = (
            f"【重要 - 第 {attempt + 1} 次纠正请求】\n"
            f"你上一次的输出校验失败，错误详情：\n"
            f"{error_detail}\n\n"
            "请严格按照要求的 JSON 格式重新输出，确保：\n"
            "1. 只输出一个 JSON 对象，不要有任何解释、注释、Markdown 标记\n"
            "2. 所有必填字段必须存在且类型正确\n"
            "3. 不要编造字段名，使用要求的字段名\n\n"
            "--- 原始需求 ---\n\n"
        )
        return correction_header + original_prompt
