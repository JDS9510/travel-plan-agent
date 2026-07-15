"""
LangGraph 全局状态定义 —— 基于 typing.TypedDict。

100% 兼容 LangGraph StateGraph，字段覆盖全流程且预留扩展位，
后续开发无需重构。

每个字段均标注更新规则（全量覆盖 / 增量追加），
方便下游节点实现正确的状态变更逻辑。
"""

from typing import TypedDict


class TravelState(TypedDict, total=False):
    """旅行行程规划 Agent 全局状态。

    贯穿 LangGraph 图编排的整个生命周期，各节点按约定规则读写状态字段。
    total=False 表示所有字段均为可选，支持渐进式状态构建。
    """

    # ---- 结构化用户需求 ----
    # 更新规则：全量覆盖 —— 在入口节点一次性写入，后续节点只读
    # 数据结构：对应 UserDemand.model_dump() 输出的字典
    user_demand: dict

    # ---- 行程整体框架 ----
    # 更新规则：全量覆盖 —— 由"需求拆解"节点生成，包含天数分配、预算拆分、每日主题
    # 数据结构：{"total_days": int, "budget_split": [...], "daily_themes": [...]}
    travel_outline: dict

    # ---- 每日行程明细 ----
    # 更新规则：全量覆盖 —— 由"行程生成"节点一次性生成全量列表
    # 数据结构：[DailyPlan.model_dump() for each day]
    daily_plans: list[dict]

    # ---- 行程校验结果 ----
    # 更新规则：全量覆盖 —— 每次校验节点重新生成
    # 数据结构：
    # {
    #     "is_pass": bool,           # 校验是否通过
    #     "issues": list[str],       # 不通过的具体问题
    #     "suggestions": list[str],  # 改进建议
    # }
    check_result: dict

    # ---- 迭代优化计数器 ----
    # 更新规则：增量追加（每次 +1） —— 由"校验"节点在每次循环时递增
    # 用途：控制循环上限，避免无限迭代（建议上限 5 次）
    iteration_count: int

    # ---- 当前执行步骤标记 ----
    # 更新规则：全量覆盖 —— 每个节点写入自身标识，便于 UI 展示进度
    # 可选值："parse_demand" | "build_outline" | "generate_plans" |
    #         "verify_plans" | "revise_plans" | "done" | "error"
    current_step: str

    # ---- 错误信息 ----
    # 更新规则：全量覆盖 —— 任意节点异常时写入，下游"错误处理"节点读取并决策
    # 空字符串表示无错误
    error_msg: str

    # ---- ReAct 决策追踪（仅在 ReAct 模式下写入） ----
    # 更新规则：全量覆盖 —— react_revise_node 写入
    # 数据结构：[
    #     {"round": 1, "thought": "...", "tool_calls": [...], "observation": "...", "timestamp": "..."},
    #     ...
    # ]
    react_trace: list[dict]

    # ---- 运行模式标记 ----
    # 更新规则：全量覆盖 —— 入口初始化时写入
    # 可选值："react"（ReAct 自主修正模式）| "classic"（原有固定流程）
    run_mode: str

    # ---- 多轮对话式修改 ----
    # 更新规则：全量覆盖
    # revision_round: 当前修订轮次（0 = 非修订模式，1-5 = 修订轮次）
    # revision_instruction: 用户当前轮次的修改指令（自然语言）
    # revision_history: 历次修改记录 [{"round": 1, "instruction": "...", "changes": [...]}]
    revision_round: int
    revision_instruction: str
    revision_history: list[dict]
