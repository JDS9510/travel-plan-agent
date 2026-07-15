"""
行程合理性校验工具 —— 校验生成的行程是否合理，输出校验结果和修改建议。

校验规则：
  1. 每日景点数量不超过 4 个，避免行程过于紧凑
  2. 单日累计游玩时长不超过 8 小时，符合正常出行强度
  3. 总预算不超过用户设定的上限
  4. 相邻景点地址区域尽量集中，避免跨城通勤

使用 langchain @tool 装饰器，100% 兼容 LangGraph 工具调用。
"""

from langchain.tools import tool

# 每日景点数量上限
_MAX_SPOTS_PER_DAY = 4
# 单日累计游玩时长上限（小时）
_MAX_DURATION_PER_DAY = 8.0


def _extract_district(address: str) -> str:
    """从地址中提取区域关键词（前两级行政区划）。"""
    if not address:
        return ""
    # 简单启发式：取"市"或"区"之前的最后一段
    for marker in ["区", "市", "县"]:
        idx = address.find(marker)
        if idx > 0:
            # 向前找一个完整区名
            start = max(0, idx - 5)
            return address[start:idx + 1]
    return address[:6]


@tool
def plan_checker(travel_plan: dict) -> dict:
    """校验生成的行程是否合理，输出校验结果和修改建议。

    参数:
        travel_plan: 完整行程字典，对应 TravelPlan 模型格式，必须包含:
            - total_budget: float 总预算上限
            - daily_plans: list[dict] 每日行程明细

    返回:
        dict: {
            "is_pass": bool,       # 是否通过所有校验
            "issues": list[str],   # 不通过项列表
            "suggestions": list[str],  # 修改建议列表
        }
    """
    daily_plans: list[dict] = travel_plan.get("daily_plans", [])
    total_budget: float = travel_plan.get("total_budget", 0.0)
    issues: list[str] = []
    suggestions: list[str] = []

    total_amount = 0.0

    for plan in daily_plans:
        day_index = plan.get("day_index", 0)
        spots = plan.get("spots", [])
        spot_count = len(spots)

        # ---- 规则1：每日景点数量校验 ----
        if spot_count > _MAX_SPOTS_PER_DAY:
            issues.append(
                f"第 {day_index} 天安排了 {spot_count} 个景点，"
                f"超过上限 {_MAX_SPOTS_PER_DAY} 个，行程过于紧凑"
            )
            suggestions.append(
                f"第 {day_index} 天建议保留 {_MAX_SPOTS_PER_DAY} 个核心景点，"
                f"其余景点移至次日或舍弃"
            )

        # ---- 规则2：单日累计游玩时长校验 ----
        total_duration = sum(s.get("duration", 0.0) for s in spots)
        if total_duration > _MAX_DURATION_PER_DAY:
            issues.append(
                f"第 {day_index} 天累计游玩时长 {total_duration:.1f} 小时，"
                f"超过上限 {_MAX_DURATION_PER_DAY} 小时"
            )
            suggestions.append(
                f"第 {day_index} 天建议缩短个别景点停留时间或减少景点数量，"
                f"控制总时长在 {_MAX_DURATION_PER_DAY} 小时以内"
            )

        # ---- 规则4：相邻景点区域集中度校验 ----
        if spot_count >= 2:
            districts = [_extract_district(s.get("address", "")) for s in spots]
            unique_districts = set(d for d in districts if d)
            if len(unique_districts) >= 3:
                issues.append(
                    f"第 {day_index} 天景点分布在 {len(unique_districts)} 个不同区域，"
                    f"区域跨度较大"
                )
                suggestions.append(
                    f"第 {day_index} 天建议将景点按区域重新分组，"
                    f"同一区域景点集中安排在同一天减少通勤"
                )

        # 累加当日预算
        total_amount += plan.get("daily_budget", 0.0)

    # ---- 规则3：总预算校验 ----
    total_amount = round(total_amount, 2)
    if total_amount > total_budget:
        issues.append(
            f"行程总预算 {total_amount} 元，超出预算上限 {total_budget} 元"
        )
        suggestions.append(
            "建议降低高门票景点比例，替换为免费景点或降低餐饮人均消费"
        )

    is_pass = len(issues) == 0

    return {
        "is_pass": is_pass,
        "issues": issues,
        "suggestions": suggestions,
    }
