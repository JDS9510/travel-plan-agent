"""
预算统计工具 —— 汇总完整行程的总预算，判断是否超支并给出调整建议。

使用 langchain @tool 装饰器，100% 兼容 LangGraph 工具调用。
"""

from langchain.tools import tool


_DEFAULT_SAFETY_MARGIN = 0.10  # 10% 弹性预留建议


def _build_daily_detail(daily_plans: list[dict]) -> list[dict]:
    """从每日行程中提取预算明细。"""
    details: list[dict] = []
    for plan in daily_plans:
        day_index = plan.get("day_index", 0)
        daily_budget = plan.get("daily_budget", 0.0)

        # 从景点中拆分门票与其它费用
        spots = plan.get("spots", [])
        ticket_total = sum(s.get("ticket_price", 0.0) for s in spots)

        details.append({
            "day_index": day_index,
            "theme": plan.get("theme", ""),
            "daily_budget": round(daily_budget, 2),
            "ticket_total": round(ticket_total, 2),
            "other_expense": round(daily_budget - ticket_total, 2),
        })
    return details


@tool
def budget_calculator(
    daily_plans: list[dict],
    total_budget_limit: float,
) -> dict:
    """统计完整行程的总预算，判断是否超出用户预算上限，给出预算明细和调整建议。

    参数:
        daily_plans: 每日行程列表，对应 DailyPlan 模型字典格式
        total_budget_limit: 总预算上限（单位：元）

    返回:
        dict: {
            "total_amount": float,        # 实际总预算
            "total_budget_limit": float,  # 用户预算上限
            "is_over": bool,              # 是否超支
            "safety_amount": float,       # 建议安全预留（10%）
            "daily_budget_detail": list,  # 每日预算明细
            "suggestions": list[str],     # 调整建议
        }
    """
    total_amount = sum(
        plan.get("daily_budget", 0.0) for plan in daily_plans
    )
    total_amount = round(total_amount, 2)
    safety_amount = round(total_budget_limit * _DEFAULT_SAFETY_MARGIN, 2)
    is_over = total_amount > total_budget_limit
    daily_detail = _build_daily_detail(daily_plans)

    # 生成建议
    suggestions: list[str] = []
    if is_over:
        over_amount = round(total_amount - total_budget_limit, 2)
        suggestions.append(
            f"当前行程总预算 {total_amount} 元，超出预算上限 "
            f"{total_budget_limit} 元，超出 {over_amount} 元"
        )
        suggestions.append(
            "建议优先压缩高门票景点，替换为免费或低价同级替代景点（如公园、博物馆）"
        )
        suggestions.append(
            "减少人均餐饮预算，优先选择本地小吃街或平价餐馆"
        )
        # 找到单日预算最高的天
        if daily_detail:
            max_day = max(daily_detail, key=lambda d: d["daily_budget"])
            suggestions.append(
                f"第 {max_day['day_index']} 天（{max_day['theme']}）"
                f"单日预算 {max_day['daily_budget']} 元为最高，"
                f"建议优先压缩该天安排"
            )
    else:
        remaining = round(total_budget_limit - total_amount, 2)
        suggestions.append(
            f"当前行程总预算 {total_amount} 元，在预算上限 "
            f"{total_budget_limit} 元以内，剩余 {remaining} 元"
        )

    # 安全预留建议（无论是否超支都提示）
    suggestions.append(
        f"建议预留 {safety_amount} 元作为应急安全金（{_DEFAULT_SAFETY_MARGIN:.0%} 弹性空间）"
    )

    return {
        "total_amount": total_amount,
        "total_budget_limit": total_budget_limit,
        "is_over": is_over,
        "safety_amount": safety_amount,
        "daily_budget_detail": daily_detail,
        "suggestions": suggestions,
    }
