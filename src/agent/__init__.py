"""
Agent 核心逻辑包 —— 对外暴露 run_travel_planner / revise_travel_plan 入口函数。

使用方式:
    from src.agent import run_travel_planner, revise_travel_plan

    # 首次生成行程
    result = run_travel_planner({
        "destination": "成都",
        "days": 3,
        "total_budget": 3000,
        "people": "一家三口",
        "preferences": ["亲子", "美食"],
    })
    print(len(result["daily_plans"]))  # → 3

    # 对话式修改
    revised = revise_travel_plan(
        original_result=result,
        instruction="把第二天的景点换成亲子类",
    )

内部实现基于 LangGraph StateGraph，节点、状态、编排细节不对外暴露。
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

logger = logging.getLogger(__name__)


def _validate_demand_params(demand: dict[str, Any]) -> str:
    """校验用户需求参数合法性，返回空字符串表示通过，否则返回错误描述。

    校验规则：
    - destination: 非空，不超过 50 字符
    - days: ≥1 且 ≤30
    - total_budget: ≥0 且 ≤1,000,000
    - people: 非空，不超过 200 字符
    """
    destination = str(demand.get("destination", "")).strip()
    if not destination:
        return "目的地不能为空，请输入有效的城市名称（如'成都'、'杭州'）"
    if len(destination) > 50:
        return f"目的地名称过长（{len(destination)}字符），请使用标准城市名称（不超过50字符）"

    days = demand.get("days", 1)
    if isinstance(days, str):
        try:
            days = int(days)
        except ValueError:
            return f"出行天数格式错误: '{days}'，请输入有效整数（如 3）"
    try:
        days = int(days)
    except (ValueError, TypeError):
        return f"出行天数格式错误，请输入有效整数（如 3）"
    if days < 1:
        return "出行天数必须 ≥1 天"
    if days > 30:
        return f"出行天数过长（{days}天），目前最多支持 30 天行程，请缩短或分次规划"

    budget = demand.get("total_budget", 0)
    if isinstance(budget, str):
        try:
            budget = float(budget)
        except ValueError:
            return f"预算格式错误: '{budget}'，请输入有效数字（如 2000）"
    try:
        budget = float(budget)
    except (ValueError, TypeError):
        return f"预算格式错误，请输入有效数字（如 2000）"
    if budget < 0:
        return "预算不能为负数"
    if budget > 1_000_000:
        return f"预算金额过大（{budget:,.0f}元），单次行程预算上限为 100 万元"

    people = str(demand.get("people", "")).strip()
    if not people:
        return "出行人群不能为空，请描述出行人数和关系（如'2人情侣'、'一家三口'）"
    if len(people) > 200:
        return f"出行人群描述过长（{len(people)}字符），请精简到 200 字符以内"

    return ""


def run_travel_planner(user_demand_dict: dict[str, Any]) -> dict[str, Any]:
    """执行旅行行程规划全流程，返回最终状态字典。

    返回的字典包含 TravelState 全部字段：
        - user_demand:   结构化用户需求
        - travel_outline: 行程框架（含 final_result）
        - daily_plans:   每日行程明细列表
        - check_result:  校验结果
        - iteration_count: 迭代次数
        - current_step:  当前步骤标记
        - error_msg:     错误信息（空字符串表示无错误）
        - react_trace:   ReAct 推理追踪
        - run_mode:      运行模式标记
        - revision_round: 修订轮次（生成模式为 0）
        - revision_instruction: 修订指令（生成模式为空）
        - revision_history: 修订历史（生成模式为空列表）

    Args:
        user_demand_dict: 用户需求字典，包含:
            - destination: str   目的地城市（必填）
            - days: int          出行天数（必填，>=1）
            - total_budget: float  总预算上限（必填，>=0）
            - people: str        出行人群描述（必填）
            - preferences: list[str]  偏好标签（可选，默认 []）
            - remark: str        补充要求（可选，默认 ""）

    Returns:
        dict: TravelState 完整状态字典，始终包含 daily_plans 字段。
              出错时 error_msg 非空，daily_plans 为空列表。
    """
    from src.agent.graph import travel_planner_graph

    try:
        # ---- 类型强制转换，防止 LLM 接收非法类型 ----
        days = user_demand_dict.get("days", 1)
        if isinstance(days, str):
            try:
                days = int(days)
            except ValueError:
                days = 1
        days = max(1, int(days))

        total_budget = user_demand_dict.get("total_budget", 0)
        if isinstance(total_budget, str):
            try:
                total_budget = float(total_budget)
            except ValueError:
                total_budget = 0
        total_budget = max(0.0, float(total_budget))

        preferences = user_demand_dict.get("preferences", [])
        if not isinstance(preferences, list):
            preferences = []

        # ---- 参数语义校验（前置拦截，不进入图执行） ----
        validation_error = _validate_demand_params(user_demand_dict)
        if validation_error:
            logger.warning("参数校验拦截: %s", validation_error)
            return {
                "user_demand": {
                    "destination": str(user_demand_dict.get("destination", "")),
                    "days": max(1, int(days)),
                    "total_budget": max(0.0, float(total_budget)),
                    "people": str(user_demand_dict.get("people", "")),
                    "preferences": preferences,
                    "remark": str(user_demand_dict.get("remark", "")),
                },
                "travel_outline": {},
                "daily_plans": [],
                "check_result": {
                    "is_pass": False,
                    "issues": [validation_error],
                    "suggestions": ["请修正参数后重新提交"],
                    "budget_detail": [],
                },
                "iteration_count": 0,
                "current_step": "error",
                "error_msg": f"参数校验失败: {validation_error}",
                "react_trace": [],
                "run_mode": "react",
                "revision_round": 0,
                "revision_instruction": "",
                "revision_history": [],
            }

        # ---- 初始化 TravelState：补齐全部字段 ----
        import os as _os
        initial_state: dict[str, Any] = {
            "user_demand": {
                "destination": str(user_demand_dict.get("destination", "")),
                "days": days,
                "total_budget": total_budget,
                "people": str(user_demand_dict.get("people", "")),
                "preferences": preferences,
                "remark": str(user_demand_dict.get("remark", "")),
            },
            "travel_outline": {},
            "daily_plans": [],
            "check_result": {},
            "iteration_count": 0,
            "current_step": "",
            "error_msg": "",
            "react_trace": [],
            "run_mode": _os.getenv("TRAVEL_PLANNER_MODE", "react").strip().lower(),
            # 修订模式字段
            "revision_round": 0,
            "revision_instruction": "",
            "revision_history": [],
        }

        # ---- 执行 LangGraph 全流程 ----
        final_state = travel_planner_graph.invoke(initial_state)
        result = dict(final_state)

        # ---- 出口守卫：杜绝空结果"假成功" ----
        daily_plans = result.get("daily_plans", [])
        error_msg = result.get("error_msg", "")
        current_step = result.get("current_step", "")

        if not error_msg and current_step != "error":
            if not daily_plans or len(daily_plans) == 0:
                logger.error("出口守卫拦截: daily_plans 为空但无错误标记")
                result["error_msg"] = "行程生成失败: 未产生任何有效的每日行程计划，请重试"
                result["current_step"] = "error"
            elif not any(
                len(day.get("spots", [])) > 0
                and any(s.get("name", "").strip() for s in day.get("spots", []))
                for day in daily_plans
            ):
                logger.error("出口守卫拦截: 所有天均无有效景点数据")
                result["error_msg"] = "行程生成失败: 每日计划中无有效景点数据，请检查目的地是否有足够景点"
                result["current_step"] = "error"

        return result

    except Exception as exc:
        # 全局异常捕获：错误写入 error_msg，返回完整状态结构
        return {
            "user_demand": {
                "destination": str(user_demand_dict.get("destination", "")),
                "days": user_demand_dict.get("days", 1),
                "total_budget": user_demand_dict.get("total_budget", 0),
                "people": str(user_demand_dict.get("people", "")),
                "preferences": user_demand_dict.get("preferences", []),
                "remark": str(user_demand_dict.get("remark", "")),
            },
            "travel_outline": {},
            "daily_plans": [],
            "check_result": {},
            "iteration_count": 0,
            "current_step": "error",
            "error_msg": f"行程规划执行失败: {str(exc)}\n{traceback.format_exc()}",
            "react_trace": [],
            "run_mode": "react",
            "revision_round": 0,
            "revision_instruction": "",
            "revision_history": [],
        }


def revise_travel_plan(
    original_result: dict[str, Any],
    instruction: str,
) -> dict[str, Any]:
    """基于自然语言指令增量修改已有行程，保留未涉及天数不变。

    流程：
        revise_intent（解析指令）→ daily_fill（定向修改）
        → plan_check（校验）→ react_revise ⇄ daily_fill
        → result_summary（汇总）

    最多支持 5 轮连续修订，每轮在上一轮结果基础上叠加。

    Args:
        original_result: 原始行程结果字典（run_travel_planner 的返回值或上一轮 revise 的返回值）。
        instruction: 修改指令（自然语言），如 "把第二天的景点换成亲子类"。

    Returns:
        dict: TravelState 完整状态字典（与 run_travel_planner 同构）。
    """
    from src.agent.graph import travel_planner_graph

    try:
        # ---- 从原始结果中提取状态 ----
        user_demand = original_result.get("user_demand", {})
        daily_plans = original_result.get("daily_plans", [])
        travel_outline = original_result.get("travel_outline", {})
        revision_history = original_result.get("revision_history", [])
        current_round = original_result.get("revision_round", 0) + 1

        # ---- 校验轮次上限 ----
        _MAX_REVISION_ROUNDS = 5
        if current_round > _MAX_REVISION_ROUNDS:
            return {
                **original_result,
                "error_msg": (
                    f"已达到最大修订轮次上限（{_MAX_REVISION_ROUNDS} 轮），"
                    "请重新提交行程生成请求。"
                ),
            }

        # ---- 构造修订初始状态 ----
        import os as _os
        initial_state: dict[str, Any] = {
            "user_demand": user_demand,
            "travel_outline": travel_outline,
            "daily_plans": daily_plans,
            "check_result": {},
            "iteration_count": 0,
            "current_step": "",
            "error_msg": "",
            "react_trace": [],
            "run_mode": _os.getenv("TRAVEL_PLANNER_MODE", "react").strip().lower(),
            # 修订模式
            "revision_round": current_round,
            "revision_instruction": instruction,
            "revision_history": revision_history,
        }

        # ---- 执行图（入口路由自动跳到 revise_intent） ----
        final_state = travel_planner_graph.invoke(initial_state)
        return dict(final_state)

    except Exception as exc:
        return {
            **original_result,
            "error_msg": f"行程修订失败: {str(exc)}\n{traceback.format_exc()}",
        }


__all__ = [
    "run_travel_planner",
    "revise_travel_plan",
]
