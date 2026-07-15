"""
ReAct 自主决策修正节点 —— 基于 LangGraph ToolNode 规范实现。

核心逻辑：
- 接收 plan_check 产生的校验问题（issues/suggestions）
- 将 4 个工具绑定到 LLM（spot_retriever / budget_calculator / plan_checker / weather_query）
- LLM 自主推理需要调用哪个工具 → 调用工具获取数据 → 综合工具结果生成定向修正方案
- 输出仅覆盖有问题的天数，而非全量重新生成

与原有流程兼容：
- 通过环境变量 TRAVEL_PLANNER_MODE 切换模式（react/classic），默认 react
- 保留 3 次迭代上限的防护逻辑，与 nodes.py 中 _MAX_ITERATIONS 一致
- 所有推理过程与工具调用纳入 AgentTracer 追踪
"""

from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.tools import budget_calculator, plan_checker, spot_retriever, weather_query

# ---------------------------------------------------------------
# 配置
# ---------------------------------------------------------------
_MAX_REACT_ROUNDS = 3  # ReAct 思考-行动-观察 循环上限

# 环境变量控制运行模式：react（默认）| classic
# 优先读取 TRAVEL_PLANNER_USE_REACT（布尔型兼容），其次 TRAVEL_PLANNER_MODE
_USE_REACT = os.getenv("TRAVEL_PLANNER_USE_REACT", "").strip().lower()
_MODE = os.getenv("TRAVEL_PLANNER_MODE", "").strip().lower()
if _MODE:
    _PLANNER_MODE = _MODE
elif _USE_REACT in ("false", "0", "no", "off"):
    _PLANNER_MODE = "classic"
else:
    _PLANNER_MODE = "react"  # 默认开启 ReAct


def is_react_mode() -> bool:
    """检查当前是否为 ReAct 自主修正模式。"""
    return _PLANNER_MODE == "react"


# ---------------------------------------------------------------
# 工具索引（供 ReAct 节点使用）
# ---------------------------------------------------------------
_REACT_TOOLS: list[Any] = [spot_retriever, budget_calculator, plan_checker, weather_query]

_TOOL_BY_NAME: dict[str, Any] = {t.name: t for t in _REACT_TOOLS}

# 工具描述（注入 System Prompt）
_TOOL_DESCRIPTIONS = """
## 可用工具

1. **spot_retriever**(destination: str, tags: list[str]) → list[dict]
   根据目的地和偏好标签搜索景点，返回景点列表（名称/地址/时长/门票/标签/推荐理由）。
   用途：预算超支时找低价替代景点；景点不合理时重新检索。

2. **budget_calculator**(daily_plans: list[dict], total_budget_limit: float) → dict
   统计总预算，返回是否超支、每日明细和调整建议。
   用途：精确分析哪些天超支最严重。

3. **plan_checker**(travel_plan: dict) → dict
   校验行程合理性（景点数量/游玩时长/区域跨度/预算），输出问题和建议。
   用途：校验修正后的局部行程是否已解决问题。

4. **weather_query**(destination: str, month: int) → dict
   查询目的地月度天气概况与穿搭建议。
   用途：考虑天气因素调整行程安排。
"""


# ---------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------
def _parse_to_dict(raw: str) -> dict[str, Any]:
    """将 LLM 文本输出解析为 dict，带 Markdown 清洗与截断修复。"""
    if not raw or not raw.strip():
        return {}
    cleaned = raw.strip()
    for prefix in ("```json", "```JSON", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    brace_start = cleaned.find("{")
    if brace_start == -1:
        return {}
    cleaned = cleaned[brace_start:]

    # 按嵌套顺序补全括号
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
        for i in range(len(cleaned), 0, -1):
            try:
                return json.loads(cleaned[:i])
            except json.JSONDecodeError:
                continue
        return {}


def _invoke_tool_safe(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """安全调用单个工具，返回统一结构。"""
    tool = _TOOL_BY_NAME.get(tool_name)
    if tool is None:
        return {"success": False, "error": f"未知工具: {tool_name}", "data": None}

    start_ts = time.time()
    try:
        result = tool.invoke(params)
        duration_ms = round((time.time() - start_ts) * 1000, 2)
        # 追踪
        try:
            from src.utils.tracer import get_tracer
            get_tracer().trace_tool_call(
                tool_name=f"react:{tool_name}",
                params={k: str(v)[:100] for k, v in params.items()},
                result=(str(result)[:300] if result else None),
                duration_ms=duration_ms,
            )
        except Exception:
            pass
        return {"success": True, "error": None, "data": result, "duration_ms": duration_ms}
    except Exception as exc:
        duration_ms = round((time.time() - start_ts) * 1000, 2)
        return {"success": False, "error": str(exc), "data": None, "duration_ms": duration_ms}


# ---------------------------------------------------------------
# ReAct 修正节点
# ---------------------------------------------------------------
def react_revise_node(state: dict[str, Any]) -> dict[str, Any]:
    """ReAct 自主决策修正节点。

    读取 check_result 中的 issues/suggestions：
    1. 构建 ReAct Prompt，让 LLM 分析问题并决定工具调用
    2. 执行工具调用（最多 _MAX_REACT_ROUNDS 轮思考-行动-观察）
    3. LLM 综合工具结果，生成定向修正方案（仅覆盖问题天数）
    4. 将修正方案写入 check_result.react_revise_plan

    状态读写：
        - 读取: user_demand, daily_plans, check_result, travel_outline
        - 写入: check_result（补充 react_analysis / react_revise_plan / react_tool_results）
        - 写入: react_trace（全量覆盖）
        - 写入: current_step → "react_revise"
    """
    try:
        # ---- 提取上下文 ----
        user_demand: dict[str, Any] = state.get("user_demand", {})
        daily_plans: list[dict[str, Any]] = state.get("daily_plans", [])
        check_result: dict[str, Any] = state.get("check_result", {})
        outline: dict[str, Any] = state.get("travel_outline", {})
        iteration_count: int = state.get("iteration_count", 0)

        destination = user_demand.get("destination", "")
        days = max(1, int(user_demand.get("days", 1)))
        total_budget = float(user_demand.get("total_budget", 0))
        preferences = user_demand.get("preferences", [])
        people = user_demand.get("people", "")
        remark = user_demand.get("remark", "")

        issues: list[str] = check_result.get("issues", [])
        suggestions: list[str] = check_result.get("suggestions", [])
        is_pass: bool = check_result.get("is_pass", False)

        # 已通过校验则无需修正
        if is_pass or not issues:
            return {
                "current_step": "react_revise",
                "react_trace": [],
                "check_result": {
                    **check_result,
                    "react_analysis": "校验已通过，无需修正",
                    "react_revise_plan": None,
                    "react_tool_results": [],
                },
            }

        # ---- 构建 ReAct Prompt ----
        # 估算当前总花费
        current_total = sum(d.get("daily_budget", 0) for d in daily_plans)

        # 构造每日行程摘要
        daily_summaries: list[dict[str, Any]] = []
        for dp in daily_plans:
            day_idx = dp.get("day_index", 0)
            spots_list = dp.get("spots", [])
            spot_names = [s.get("name", "") for s in spots_list]
            daily_summaries.append({
                "day_index": day_idx,
                "theme": dp.get("theme", ""),
                "spots": spot_names,
                "daily_budget": dp.get("daily_budget", 0),
                "food": dp.get("food_recommendation", []),
            })

        system_prompt = f"""你是一位经验丰富的旅行行程修正专家。当前行程校验未通过，你需要自主分析问题并调用工具获取修正所需信息，最终生成定向修正方案。

## 行程基本信息
- 目的地: {destination}
- 天数: {days} 天
- 总预算上限: {total_budget} 元（当前估算: {current_total:.0f} 元）
- 出行人群: {people}
- 偏好标签: {json.dumps(preferences, ensure_ascii=False)}
- 补充要求: {remark or "无"}

## 当前每日行程
{json.dumps(daily_summaries, ensure_ascii=False, indent=2)}

## 校验发现的问题
{json.dumps(issues, ensure_ascii=False, indent=2)}

## 改进建议
{json.dumps(suggestions, ensure_ascii=False, indent=2)}

## 当前迭代次数: {iteration_count + 1} / 3
{_TOOL_DESCRIPTIONS}

---

请按以下步骤进行推理：

**步骤1 - 问题分析**: 分析每个问题的根本原因。是预算分配不合理？景点选择不匹配偏好？还是行程安排过紧？

**步骤2 - 工具决策**: 决定需要调用哪些工具来获取修正所需信息。例如：
- 预算超支 → 调用 budget_calculator 精确定位超支天数
- 景点不合理 → 调用 spot_retriever 搜索更合适的替代景点
- 行程过紧 → 调用 plan_checker 复核局部行程
- 天气因素 → 调用 weather_query 获取天气建议

**步骤3 - 执行推理**: 根据工具返回结果，确定具体的修正方案。

你必须用以下 JSON 格式回复（在 "tool_calls" 数组为空时表示不需要再调用工具，直接输出最终修正方案）：

{{
    "thought": "你的分析推理（一句话说明当前在做什么）",
    "tool_calls": [
        {{"tool": "工具名称", "params": {{"参数名": 参数值}}, "reason": "调用原因"}}
    ],
    "final_plan": {{
        "analysis": "综合问题分析",
        "target_days": [需要修正的天索引列表, 如 [1, 3]],
        "specific_actions": [
            {{
                "day_index": 1,
                "action": "replace_spot | adjust_budget | rearrange | add_spot | remove_spot",
                "detail": "具体修改内容描述",
                "target": "要替换/调整的具体景点名或预算项",
                "reason": "修改原因"
            }}
        ]
    }}
}}

注意：
- tool_calls 为空数组时 final_plan 必须存在
- specific_actions 只覆盖真正需要修改的天，未变动的天不要列出
- 优先替换高价景点为低价同类替代，而非简单删除
- 只输出 JSON，禁止任何解释、Markdown 标记"""

        # ---- ReAct 循环 ----
        react_trace: list[dict[str, Any]] = []
        messages: list[Any] = [HumanMessage(content=system_prompt)]
        final_plan: dict[str, Any] = {}
        tool_results_all: list[dict[str, Any]] = []

        # 获取底层 ChatOpenAI 实例并绑定工具（react_revise 走主模型）
        from src.llm.model_router import get_model_router
        chat_model = get_model_router().get_chat_openai("react_revise")
        llm_with_tools = chat_model.bind_tools(_REACT_TOOLS)

        for round_idx in range(_MAX_REACT_ROUNDS):
            round_start = time.time()

            try:
                # 调用 LLM（带工具绑定）
                response = llm_with_tools.invoke(messages)
                messages.append(response)

                round_duration_ms = round((time.time() - round_start) * 1000, 2)

                content = response.content if hasattr(response, "content") else str(response)
                tool_calls = getattr(response, "tool_calls", None) or []

                # 如果没有 tool_calls，尝试从 content 解析 final_plan
                if not tool_calls:
                    parsed = _parse_to_dict(content)
                    final_plan = parsed.get("final_plan", parsed if parsed else {})
                    react_trace.append({
                        "round": round_idx + 1,
                        "thought": parsed.get("thought", ""),
                        "tool_calls_executed": [],
                        "observation": "无需更多工具调用，已生成最终修正方案",
                        "duration_ms": round_duration_ms,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    break

                # 有 tool_calls → 执行
                round_tool_results: list[dict[str, Any]] = []
                for tc in tool_calls:
                    tool_name = tc.get("name", "") if isinstance(tc, dict) else tc.name
                    tool_args = tc.get("args", {}) if isinstance(tc, dict) else tc.args
                    tool_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")

                    result = _invoke_tool_safe(tool_name, tool_args)

                    round_tool_results.append({
                        "tool": tool_name,
                        "params": {k: str(v)[:100] for k, v in tool_args.items()},
                        "success": result["success"],
                        "data_preview": str(result.get("data", ""))[:300],
                        "error": result.get("error"),
                    })
                    tool_results_all.append(round_tool_results[-1])

                    # 构造 ToolMessage 返回给 LLM
                    observation_text = json.dumps(
                        result.get("data", {}), ensure_ascii=False, default=str
                    ) if result["success"] else f"工具调用失败: {result['error']}"
                    messages.append(ToolMessage(content=observation_text, tool_call_id=tool_id))

                # 从 content 解析思考
                parsed_thought = _parse_to_dict(content)
                thought = parsed_thought.get("thought", content[:200] if content else "")

                react_trace.append({
                    "round": round_idx + 1,
                    "thought": thought,
                    "tool_calls_executed": round_tool_results,
                    "observation": f"已执行 {len(round_tool_results)} 个工具调用",
                    "duration_ms": round_duration_ms,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })

            except Exception as exc:
                react_trace.append({
                    "round": round_idx + 1,
                    "thought": "",
                    "tool_calls_executed": [],
                    "observation": f"ReAct 循环异常: {str(exc)}",
                    "duration_ms": 0,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                break

        # ---- 兜底：ReAct 未产生 final_plan ----
        if not final_plan or not final_plan.get("specific_actions"):
            # 基于原始 issues/suggestions 构造兜底修正方案
            target_days: list[int] = []
            actions: list[dict[str, Any]] = []
            for issue in issues:
                # 尝试从问题描述中提取天数
                for i in range(1, days + 1):
                    if f"第{i}天" in issue and i not in target_days:
                        target_days.append(i)
            if not target_days:
                target_days = list(range(1, days + 1))

            for day_idx in target_days:
                actions.append({
                    "day_index": day_idx,
                    "action": "rearrange",
                    "detail": f"第{day_idx}天行程需调整: {'; '.join(issues[:2])}",
                    "target": "",
                    "reason": "校验未通过，需要重新安排",
                })

            final_plan = {
                "analysis": f"ReAct 未生成最终方案，使用兜底修正计划（{len(issues)} 个问题）",
                "target_days": target_days,
                "specific_actions": actions,
            }

        # ---- 合并写入 state ----
        return {
            "current_step": "react_revise",
            "react_trace": react_trace,
            "check_result": {
                **check_result,
                "react_analysis": final_plan.get("analysis", ""),
                "react_revise_plan": final_plan,
                "react_tool_results": tool_results_all,
            },
            "error_msg": "",
        }

    except Exception as exc:
        trace = traceback.format_exc()
        return {
            "current_step": "error",
            "react_trace": state.get("react_trace", []),
            "check_result": {
                **state.get("check_result", {}),
                "react_analysis": "",
                "react_revise_plan": None,
                "react_tool_results": [],
            },
            "error_msg": f"ReAct 修正节点异常: {str(exc)}\n{trace}",
        }
