"""
增量行程修改节点 —— 基于自然语言指令的定向行程调整。

核心逻辑：
- 接收用户修改指令（自然语言），解析意图并定位需修改的天数
- 保留未涉及天数的行程不变，仅定向修改目标天数
- 修改后同样触发 plan_check + react_revise 保证合理性
- 支持最多 5 轮连续对话式调整，保留修订历史与全量 diff

与原有流程兼容：
- 复用 daily_fill_node 的定向修正能力（target_days / specific_actions）
- 所有修订过程接入 AgentTracer 全链路追踪
"""

from __future__ import annotations

import json
import re
import time
import traceback
from typing import Any

from src.llm.model_router import get_model_router
from src.utils.llm_validator import LLMOutputValidator

# 最大修订轮次
_MAX_REVISION_ROUNDS = 5

# 全局校验器实例（轻量模式，少重试）
_validator = LLMOutputValidator(max_retries=2)


# ============================================================
# 内部工具函数
# ============================================================
def _parse_revise_response(raw: str) -> dict[str, Any]:
    """解析 LLM 修订意图为结构化 dict，带 Markdown 清洗与 JSON 修复。

    Args:
        raw: LLM 原始输出文本。

    Returns:
        dict: 解析后的修订方案，解析失败返回空 dict。
    """
    if not raw or not raw.strip():
        return {}
    cleaned: str = raw.strip()
    for prefix in ("```json", "```JSON", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    brace_start: int = cleaned.find("{")
    if brace_start == -1:
        return {}
    cleaned = cleaned[brace_start:]

    # 括号补全（按嵌套顺序）
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
        # 截断重试
        for i in range(len(cleaned), 0, -1):
            try:
                return json.loads(cleaned[:i])
            except json.JSONDecodeError:
                continue
        return {}


def _compute_day_diff(
    day_index: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    """计算单日行程修改前后的差异摘要。

    Args:
        day_index: 天数索引。
        before: 修改前的 DailyPlan dict（可能为 None）。
        after: 修改后的 DailyPlan dict（可能为 None）。

    Returns:
        dict: 差异摘要，包含 changed_fields 和简要说明。
    """
    diff: dict[str, Any] = {
        "day_index": day_index,
        "changed": False,
        "changed_fields": [],
        "summary": "",
    }

    if before is None and after is None:
        return diff
    if before is None:
        diff["changed"] = True
        diff["changed_fields"] = ["__all__"]
        diff["summary"] = f"第{day_index}天：新增行程"
        return diff
    if after is None:
        diff["changed"] = True
        diff["changed_fields"] = ["__all__"]
        diff["summary"] = f"第{day_index}天：行程已删除"
        return diff

    # 逐字段比较
    changes: list[str] = []

    # 主题
    if before.get("theme") != after.get("theme"):
        changes.append("theme")
        diff["summary"] += f"主题 {before.get('theme','')} → {after.get('theme','')}；"

    # 景点
    before_spots = [s.get("name", "") for s in before.get("spots", [])]
    after_spots = [s.get("name", "") for s in after.get("spots", [])]
    if before_spots != after_spots:
        changes.append("spots")
        removed = set(before_spots) - set(after_spots)
        added = set(after_spots) - set(before_spots)
        if removed:
            diff["summary"] += f"移除景点: {', '.join(removed)}；"
        if added:
            diff["summary"] += f"新增景点: {', '.join(added)}；"

    # 预算
    if before.get("daily_budget") != after.get("daily_budget"):
        changes.append("daily_budget")
        diff["summary"] += (
            f"预算 {before.get('daily_budget',0)} → {after.get('daily_budget',0)}；"
        )

    # 餐饮
    before_food = before.get("food_recommendation", [])
    after_food = after.get("food_recommendation", [])
    if before_food != after_food:
        changes.append("food_recommendation")

    # 交通
    if before.get("traffic_note") != after.get("traffic_note"):
        changes.append("traffic_note")

    diff["changed_fields"] = changes
    diff["changed"] = len(changes) > 0

    if not diff["summary"]:
        diff["summary"] = f"第{day_index}天：无变化"

    return diff


def _compute_full_diff(
    before_plans: list[dict[str, Any]],
    after_plans: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算全量修改前后的 diff 摘要。

    Args:
        before_plans: 修改前的 daily_plans 列表。
        after_plans: 修改后的 daily_plans 列表。

    Returns:
        dict: 包含 per_day_diffs、total_changed_days、summary。
    """
    before_by_day: dict[int, dict[str, Any]] = {
        p.get("day_index", 0): p for p in before_plans
    }
    after_by_day: dict[int, dict[str, Any]] = {
        p.get("day_index", 0): p for p in after_plans
    }

    all_days = sorted(set(list(before_by_day.keys()) + list(after_by_day.keys())))
    per_day_diffs: list[dict[str, Any]] = []
    changed_days: list[int] = []

    for d in all_days:
        day_diff = _compute_day_diff(d, before_by_day.get(d), after_by_day.get(d))
        per_day_diffs.append(day_diff)
        if day_diff["changed"]:
            changed_days.append(d)

    summary_parts: list[str] = []
    for dd in per_day_diffs:
        if dd["changed"]:
            summary_parts.append(dd["summary"])

    return {
        "per_day_diffs": per_day_diffs,
        "total_changed_days": len(changed_days),
        "changed_day_indices": changed_days,
        "summary": " | ".join(summary_parts) if summary_parts else "行程无变化",
    }


# ============================================================
# 修订意图解析节点
# ============================================================
def revise_intent_node(state: dict[str, Any]) -> dict[str, Any]:
    """修订意图解析节点 —— 将自然语言指令转为定向修改方案。

    状态读写：
        - 读取: user_demand, daily_plans, revision_instruction,
                revision_round, revision_history
        - 写入: check_result（react_revise_plan / issues / suggestions）
        - 写入: current_step → "revise_intent"
        - 写入: revision_history（追加本轮记录，含修改前快照）
    """
    try:
        instruction: str = str(state.get("revision_instruction", "")).strip()
        daily_plans: list[dict[str, Any]] = state.get("daily_plans", [])
        user_demand: dict[str, Any] = state.get("user_demand", {})
        revision_round: int = int(state.get("revision_round", 1))
        revision_history: list[dict[str, Any]] = list(
            state.get("revision_history", []) or []
        )

        # ---- 参数校验 ----
        if not instruction:
            return {
                "current_step": "error",
                "error_msg": "修改指令为空，请提供具体的调整说明。",
            }

        if not daily_plans:
            return {
                "current_step": "error",
                "error_msg": "当前行程为空，无法执行修改。请先生成原始行程。",
            }

        if revision_round > _MAX_REVISION_ROUNDS:
            return {
                "current_step": "error",
                "error_msg": (
                    f"已达到最大修订轮次上限（{_MAX_REVISION_ROUNDS} 轮），"
                    "请重新提交行程生成请求。"
                ),
            }

        days_count: int = len(daily_plans)

        # ---- 构造每日行程摘要供 LLM 分析 ----
        daily_summaries: list[dict[str, Any]] = []
        for dp in daily_plans:
            day_idx: int = dp.get("day_index", 0)
            spots_list: list[dict[str, Any]] = dp.get("spots", [])
            spot_names: list[str] = [s.get("name", "") for s in spots_list]
            daily_summaries.append({
                "day_index": day_idx,
                "theme": dp.get("theme", ""),
                "spots": spot_names,
                "daily_budget": dp.get("daily_budget", 0),
                "food": dp.get("food_recommendation", [])[:3],
                "traffic_note": dp.get("traffic_note", ""),
            })

        destination: str = str(user_demand.get("destination", ""))
        preferences: list[str] = list(user_demand.get("preferences", []))
        total_budget: float = float(user_demand.get("total_budget", 0))
        people: str = str(user_demand.get("people", ""))

        prompt: str = f"""你是旅行行程修改专家。用户对已生成的行程提出了调整要求，你需要精准解析意图并生成定向修改方案。

## 用户修改指令
"{instruction}"

## 行程上下文
- 目的地: {destination} | 天数: {days_count} 天 | 总预算: {total_budget} 元 | 人群: {people}
- 偏好: {json.dumps(preferences, ensure_ascii=False)}

## 当前行程
{json.dumps(daily_summaries, ensure_ascii=False, indent=2)}

## 历史修订（第 {revision_round} 轮）
{json.dumps(revision_history, ensure_ascii=False, indent=2) if revision_history else "（首次修订）"}

---

请分析修改指令并输出纯 JSON：

{{
    "analysis": "对修改指令的简要分析（1-2 句话）",
    "target_days": [需要修改的天索引列表，如 [1, 3]]，
    "specific_actions": [
        {{
            "day_index": 1,
            "action": "replace_spot | adjust_budget | rearrange | add_spot | remove_spot | change_theme | update_food",
            "detail": "具体修改内容描述",
            "target": "要替换/调整的具体景点名或预算项",
            "reason": "修改原因"
        }}
    ],
    "instruction_type": "spot_swap | budget_fix | theme_change | food_update | general_rearrange"
}}

注意：
- target_days 只列出真正需要改的天，其余天保持不变
- 如果是全局性调整（如"总预算降为2000"），列出所有受影响的天的具体调整方案
- 如果指令涉及的景点不在当前行程中，action 设为 "add_spot" 并说明要搜索什么类型的景点
- 如果指令模糊（如"优化一下"），根据偏好和预算给出合理的优化方向
- 只输出 JSON，不输出任何解释文字、Markdown 标记"""

        # ---- 调用 LLM 解析意图（revise_intent 走小模型降本） ----
        start_ts: float = time.time()
        response_text: str = ""
        llm_error: str | None = None
        try:
            router = get_model_router()
            response_text = router.route("revise_intent", prompt, temperature=0.2)
        except Exception as exc:
            llm_error = str(exc)
            response_text = json.dumps(
                _fallback_parse_instruction(instruction, daily_plans, user_demand),
                ensure_ascii=False,
            )

        duration_ms: float = round((time.time() - start_ts) * 1000, 2)

        # ---- 解析 LLM 输出 ----
        parsed: dict[str, Any] = _parse_revise_response(response_text)
        if not parsed or not parsed.get("specific_actions"):
            parsed = _fallback_parse_instruction(instruction, daily_plans, user_demand)

        # ---- 校验 target_days 合法性 ----
        target_days: list[int] = [
            int(d) for d in parsed.get("target_days", [])
            if isinstance(d, (int, float)) and 1 <= int(d) <= days_count
        ]
        if not target_days:
            # 无法定位 → 全部标记为待修改
            target_days = list(range(1, days_count + 1))
        parsed["target_days"] = target_days

        specific_actions: list[dict[str, Any]] = parsed.get("specific_actions", [])

        # ---- 全链路追踪 ----
        try:
            from src.utils.tracer import get_tracer
            tracer = get_tracer()
            tracer.trace_llm_call(
                prompt_preview=prompt[:200],
                response_preview=response_text[:200],
                duration_ms=duration_ms,
                error=llm_error,
            )
            tracer.write_event("revise_intent", {
                "revision_round": revision_round,
                "instruction": instruction,
                "instruction_length": len(instruction),
                "target_days": target_days,
                "actions_count": len(specific_actions),
                "instruction_type": parsed.get("instruction_type", ""),
                "llm_error": llm_error,
                "llm_duration_ms": duration_ms,
                "fallback_used": bool(llm_error or not parsed.get("specific_actions")),
            })
        except Exception:
            pass

        # ---- 构造 check_result 以复用 daily_fill_node 定向修正逻辑 ----
        react_revise_plan: dict[str, Any] = {
            "analysis": parsed.get(
                "analysis",
                f"根据指令「{instruction[:50]}...」定向修改",
            ),
            "target_days": target_days,
            "specific_actions": specific_actions,
        }

        # 构造校验问题（驱动 daily_fill → plan_check → react_revise 循环）
        fake_issues: list[str] = [
            f"用户修改要求: {instruction}",
            f"需修改天数: {target_days}",
        ]
        fake_suggestions: list[str] = [
            f"根据用户指令定向调整第{d}天行程"
            for d in target_days
        ]

        # ---- 追加修订历史（含修改前快照） ----
        new_history: list[dict[str, Any]] = list(revision_history)
        new_history.append({
            "round": revision_round,
            "instruction": instruction,
            "analysis": parsed.get("analysis", ""),
            "target_days": target_days,
            "instruction_type": parsed.get("instruction_type", ""),
            "actions": [
                {
                    "day": a.get("day_index"),
                    "action": a.get("action"),
                    "detail": str(a.get("detail", ""))[:200],
                    "target": str(a.get("target", ""))[:100],
                    "reason": str(a.get("reason", ""))[:200],
                }
                for a in specific_actions
            ],
            # 保存修改前每日行程快照（仅受影响的 days），供 diff 计算
            "before_snapshot": {
                str(dp.get("day_index", 0)): {
                    "theme": dp.get("theme", ""),
                    "spots": [s.get("name", "") for s in dp.get("spots", [])],
                    "daily_budget": dp.get("daily_budget", 0),
                    "food": dp.get("food_recommendation", [])[:3],
                }
                for dp in daily_plans
                if dp.get("day_index", 0) in target_days
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })

        return {
            "current_step": "revise_intent",
            "revision_history": new_history,
            "check_result": {
                "is_pass": False,
                "issues": fake_issues,
                "suggestions": fake_suggestions,
                "react_analysis": parsed.get("analysis", ""),
                "react_revise_plan": react_revise_plan,
                "react_tool_results": [],
                "budget_detail": [],
            },
            "iteration_count": 0,  # 每轮修订重置迭代计数
            "error_msg": "",
        }

    except Exception as exc:
        trace: str = traceback.format_exc()
        return {
            "current_step": "error",
            "revision_history": state.get("revision_history", []),
            "error_msg": f"修订意图解析异常: {str(exc)}\n{trace}",
        }


# ============================================================
# 兜底解析：LLM 不可用时基于关键词匹配
# ============================================================
def _fallback_parse_instruction(
    instruction: str,
    daily_plans: list[dict[str, Any]],
    user_demand: dict[str, Any],
) -> dict[str, Any]:
    """当 LLM 不可用时，基于关键词做兜底解析。

    覆盖常见修改意图：景点替换、预算调整、顺序重排、餐饮更新、主题变更。

    Args:
        instruction: 用户修改指令。
        daily_plans: 当前每日行程列表。
        user_demand: 用户需求字典。

    Returns:
        dict: 结构化修订方案（与 LLM 输出格式一致）。
    """
    days_count: int = len(daily_plans)
    target_days: list[int] = []
    actions: list[dict[str, Any]] = []

    # ---- 关键词匹配：提取天数 ----
    # 支持: "第1天", "第二天", "第 3 天", "第一天和第三天", "第1-3天"
    day_matches: list[str] = re.findall(r'第\s*(\d+)\s*天', instruction)
    for m in day_matches:
        d: int = int(m)
        if 1 <= d <= days_count and d not in target_days:
            target_days.append(d)

    # 中文数字 → 阿拉伯数字
    cn_num_map: dict[str, int] = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    for cn, num in cn_num_map.items():
        if f"第{cn}天" in instruction and num <= days_count and num not in target_days:
            target_days.append(num)

    # "所有天" / "全部" / "每天" → 全部
    if any(w in instruction for w in ["所有天", "全部", "每天", "整体", "整个"]):
        target_days = list(range(1, days_count + 1))

    if not target_days:
        # 无法定位具体天数 → 修改全部
        target_days = list(range(1, days_count + 1))

    # ---- 关键词匹配：动作类型 ----
    instruction_lower: str = instruction.lower()

    if any(w in instruction_lower for w in [
        "景点", "换成", "替换", "更换", "换一个", "换掉", "改成",
        "改为", "换成", "不要", "去掉", "删除", "移除",
    ]):
        action: str = "replace_spot"
    elif any(w in instruction_lower for w in [
        "预算", "花费", "超支", "省钱", "降", "减", "费用",
        "价格", "贵", "便宜",
    ]):
        action = "adjust_budget"
    elif any(w in instruction_lower for w in [
        "顺序", "安排", "紧凑", "宽松", "节奏", "调整",
        "重新排", "换顺序",
    ]):
        action = "rearrange"
    elif any(w in instruction_lower for w in [
        "餐饮", "美食", "吃的", "饭", "餐厅", "小吃", "火锅",
    ]):
        action = "update_food"
    elif any(w in instruction_lower for w in [
        "主题", "风格", "类型", "亲子", "情侣", "文艺",
    ]):
        action = "change_theme"
    else:
        action = "general_rearrange"

    for d in target_days:
        actions.append({
            "day_index": d,
            "action": action,
            "detail": instruction,
            "target": "",
            "reason": f"用户指令: {instruction[:80]}",
        })

    return {
        "analysis": f"基于关键词解析修改指令（兜底模式，非 LLM）",
        "target_days": target_days,
        "specific_actions": actions,
        "instruction_type": action,
    }
