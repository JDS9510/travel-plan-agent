"""
LangGraph 工作流编排 —— 旅行行程规划 Agent 有向图。

图结构（半并行优化 + ReAct 自主修正路由 + 多轮对话修订路由）：
  ┌─(revision_round=0)─ demand_analyze（入口）
  │                           │
  │                     spot_retrieve（景点检索，缓存优先）
  │                           │
  │              ┌────────────┼────────────┐  ← 并行
  │     outline_generate   spot_pre_check
  │      (LLM框架生成)     (规则预校验)
  │              └────────────┼────────────┘
  │                           │
  ├─(revision_round>0)─ revise_intent ──┐
  │                                     │
  └─────────────────────────────────────┘
                    │
                    ↓
              daily_fill ←──────────────────────────────────────┐
                    ↓                                            │
              fact_check （行程合理性校验，规则优先+LLM兜底）      │
                    ↓                                            │
              plan_check ──(pass | iter>=3)──→ result_summary   │
                    │                                            │
                    │ (not pass & iter<3)                        │
                    ↓                                            │
              react_revise ──(ReAct mode)──→ daily_fill ────────┘
                    │
                    │ (classic mode fallback)
                    └──→ daily_fill ─────────────────────────────┘

运行模式：
- TRAVEL_PLANNER_MODE=react（默认）：校验不通过时先走 ReAct 自主修正
- TRAVEL_PLANNER_MODE=classic：走原有硬编码修正逻辑
- revision_round > 0：进入修订模式，跳过需求解析和框架生成

每个节点只读写自身职责范围内的状态字段，状态更新由 LangGraph
按默认 Overwrite Reducer 自动合并到 TravelState 中。

全链路追踪：每个节点自动包裹 AgentTracer，记录输入输出、耗时、异常。
"""

from __future__ import annotations

from typing import Any, Literal, Union

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from src.agent.nodes import (
    daily_fill_node,
    demand_analyze_node,
    fact_check_node,
    outline_generate_node,
    plan_check_node,
    result_summary_node,
    spot_pre_check_node,
    spot_retrieve_node,
)
from src.agent.react_revise import is_react_mode, react_revise_node
from src.agent.revise_node import revise_intent_node
from src.agent.state import TravelState
from src.utils.tracer import get_tracer

# 迭代上限（与 nodes.py 保持一致）
_MAX_ITERATIONS = 3


# ============================================================
# 条件路由
# ============================================================
def _route_entry(
    state: dict[str, Any],
) -> Literal["demand_analyze", "revise_intent", "result_summary"]:
    """入口路由：修订模式跳过需求解析和框架生成。"""
    revision_round = state.get("revision_round", 0)
    if revision_round > 0:
        # 修订模式：直接进入意图解析
        if state.get("current_step") == "error" or state.get("error_msg"):
            return "result_summary"
        return "revise_intent"
    # 正常生成模式
    return "demand_analyze"


def _route_after_demand_analyze(
    state: dict[str, Any],
) -> Literal["spot_retrieve", "result_summary"]:
    """需求拆解后路由：有错误直接进入结果汇总，正常则进入景点检索。"""
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return "spot_retrieve"


def _route_parallel_after_spot(
    state: dict[str, Any],
) -> Union[Literal["result_summary"], list[Send]]:
    """景点检索后并行路由：同时启动 outline_generate + spot_pre_check。

    两个节点读取同一 _spot_pool，写入不同 state 字段（travel_outline vs check_result），
    无数据竞争，可安全并行。任一失败则直接汇总。
    """
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return [
        Send("outline_generate", state),
        Send("spot_pre_check", state),
    ]


def _route_after_outline(
    state: dict[str, Any],
) -> Literal["daily_fill", "result_summary"]:
    """框架生成后路由：正常则进入每日填充，出错则汇总。"""
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return "daily_fill"


def _route_after_spot_pre_check(
    state: dict[str, Any],
) -> Literal["daily_fill", "result_summary"]:
    """景点预校验后路由：正常则进入每日行程填充。"""
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return "daily_fill"


def _route_after_revise_intent(
    state: dict[str, Any],
) -> Literal["daily_fill", "result_summary"]:
    """修订意图解析后路由：有错误直接终止，否则进入定向填充。"""
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return "daily_fill"


def _route_after_fact_check(
    state: dict[str, Any],
) -> Literal["plan_check", "result_summary"]:
    """事实校验后路由：

    - 校验节点自身异常（current_step == error）→ 直接进入 result_summary
    - 正常 → 进入 plan_check 合并机械校验

    注意：事实校验不通过不在此处处理——plan_check 会合并结果，
    然后由 _route_after_check 统一判断是否重试。
    """
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return "plan_check"


def _route_after_check(
    state: dict[str, Any],
) -> Literal["react_revise", "daily_fill", "result_summary"]:
    """校验后路由：

    - 校验通过 → 进入 result_summary 汇总输出
    - 校验不通过 且 iteration_count < 3：
        - ReAct 模式 → 进入 react_revise 自主修正
        - Classic 模式 → 直接回到 daily_fill
    - iteration_count >= 3 → 强制进入 result_summary
    - 或出现错误标记 → 强制进入 result_summary
    """
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"

    check_result: dict[str, Any] = state.get("check_result", {})
    is_pass: bool = check_result.get("is_pass", False)
    iteration_count: int = state.get("iteration_count", 0)

    if is_pass:
        return "result_summary"

    if iteration_count >= _MAX_ITERATIONS:
        return "result_summary"

    # 不通过且未达上限 → 根据模式选择路由
    if is_react_mode():
        return "react_revise"
    else:
        return "daily_fill"


def _route_after_react_revise(
    state: dict[str, Any],
) -> Literal["daily_fill", "result_summary"]:
    """ReAct 修正后路由：

    - 修正节点自身出错 → result_summary
    - 正常 → daily_fill 执行定向修正
    """
    if state.get("current_step") == "error" or state.get("error_msg"):
        return "result_summary"
    return "daily_fill"


# ============================================================
# 构建图
# ============================================================
def build_graph():
    """构建并编译旅行行程规划 StateGraph。

    每个节点包裹 AgentTracer 追踪钩子，不侵入业务代码。
    返回编译后的 CompiledStateGraph 实例。
    """
    tracer = get_tracer()

    workflow = StateGraph(TravelState)

    # ---- 注册节点（包裹追踪钩子） ----
    workflow.add_node(
        "demand_analyze",
        tracer.wrap_node("demand_analyze", demand_analyze_node),
    )
    workflow.add_node(
        "spot_retrieve",
        tracer.wrap_node("spot_retrieve", spot_retrieve_node),
    )
    workflow.add_node(
        "outline_generate",
        tracer.wrap_node("outline_generate", outline_generate_node),
    )
    workflow.add_node(
        "spot_pre_check",
        tracer.wrap_node("spot_pre_check", spot_pre_check_node),
    )
    workflow.add_node(
        "revise_intent",
        tracer.wrap_node("revise_intent", revise_intent_node),
    )
    workflow.add_node(
        "daily_fill",
        tracer.wrap_node("daily_fill", daily_fill_node),
    )
    workflow.add_node(
        "fact_check",
        tracer.wrap_node("fact_check", fact_check_node),
    )
    workflow.add_node(
        "plan_check",
        tracer.wrap_node("plan_check", plan_check_node),
    )
    workflow.add_node(
        "react_revise",
        tracer.wrap_node("react_revise", react_revise_node),
    )
    workflow.add_node(
        "result_summary",
        tracer.wrap_node("result_summary", result_summary_node),
    )

    # ---- 编排边 ----
    # 入口路由：修订模式 vs 正常模式
    workflow.set_conditional_entry_point(
        _route_entry,
        {
            "demand_analyze": "demand_analyze",
            "revise_intent": "revise_intent",
            "result_summary": "result_summary",
        },
    )

    # 需求拆解 → 景点检索（正常）/ 结果汇总（出错）
    workflow.add_conditional_edges(
        "demand_analyze",
        _route_after_demand_analyze,
        {
            "spot_retrieve": "spot_retrieve",
            "result_summary": "result_summary",
        },
    )

    # 景点检索 → 并行：框架生成 + 景点预校验（正常）/ 结果汇总（出错）
    workflow.add_conditional_edges(
        "spot_retrieve",
        _route_parallel_after_spot,
    )

    # 框架生成 → 每日填充（正常）/ 结果汇总（出错）
    workflow.add_conditional_edges(
        "outline_generate",
        _route_after_outline,
        {
            "daily_fill": "daily_fill",
            "result_summary": "result_summary",
        },
    )

    # 景点预校验 → 每日填充（正常）/ 结果汇总（出错）— 与 outline_generate 汇合
    workflow.add_conditional_edges(
        "spot_pre_check",
        _route_after_spot_pre_check,
        {
            "daily_fill": "daily_fill",
            "result_summary": "result_summary",
        },
    )

    # 修订意图解析 → 正常则填充，出错则终止
    workflow.add_conditional_edges(
        "revise_intent",
        _route_after_revise_intent,
        {
            "daily_fill": "daily_fill",
            "result_summary": "result_summary",
        },
    )

    # 每日填充 → 事实校验
    workflow.add_edge("daily_fill", "fact_check")

    # 事实校验 → 正常则进入机械校验，出错则直接汇总
    workflow.add_conditional_edges(
        "fact_check",
        _route_after_fact_check,
        {
            "plan_check": "plan_check",
            "result_summary": "result_summary",
        },
    )

    # 校验 → 通过/达到上限 → 汇总，否则根据模式进入 ReAct 或直接回填
    workflow.add_conditional_edges(
        "plan_check",
        _route_after_check,
        {
            "react_revise": "react_revise",
            "daily_fill": "daily_fill",
            "result_summary": "result_summary",
        },
    )

    # ReAct 修正 → 正常则回填，出错则终止
    workflow.add_conditional_edges(
        "react_revise",
        _route_after_react_revise,
        {
            "daily_fill": "daily_fill",
            "result_summary": "result_summary",
        },
    )

    # 终点
    workflow.add_edge("result_summary", END)

    return workflow.compile()


# ---- 全局编译实例 ----
travel_planner_graph = build_graph()
