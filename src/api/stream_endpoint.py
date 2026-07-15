"""
SSE 流式行程生成接口 —— POST /api/travel/generate-stream

入参与 generate-async 完全一致，返回标准 SSE 事件流。
100% 复用现有景点检索、行程生成、真实性校验全链路工作流。
生成完成后写入原有任务库，兼容任务查询、文件导出。

事件格式（4 类）:
    progress: {node, step, percent, description}    进度推送
    content:  {fragment}                              Markdown 正文片段
    done:     {task_id}                               生成结束
    error:    {code, message, node?}                  错误信息
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from src.agent.graph import travel_planner_graph
from src.api.schemas import TravelPlanRequest
from src.services.cache_service import get_cache_service
from src.services.export_service import get_export_service
from src.services.task_service import TaskStatus, get_task_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Travel"])

# ---------------------------------------------------------------------------
# 节点 → (percent, step, description) 进度映射
# ---------------------------------------------------------------------------
_NODE_PROGRESS: dict[str, tuple[int, str, str]] = {
    "demand_analyze":   (8,   "parse_demand",    "正在解析出行需求…"),
    "spot_retrieve":    (15,  "spot_retrieve",   "正在检索目的地景点数据…"),
    "outline_generate": (30,  "build_outline",   "正在规划每日行程框架…"),
    "spot_pre_check":   (30,  "spot_pre_check",  "正在预校验景点信息…"),
    "revise_intent":    (20,  "revise_intent",   "正在解析修改意图…"),
    "daily_fill":       (55,  "generate_plans",  "正在生成每日行程详情…"),
    "fact_check":       (72,  "fact_check",      "正在校验行程真实性…"),
    "plan_check":       (82,  "verify_plans",    "正在执行综合校验…"),
    "react_revise":     (60,  "react_revise",    "正在智能修正行程…"),
    "result_summary":   (95,  "result_summary",  "正在汇总生成结果…"),
}

_MARKDOWN_CHUNK_SIZE = 300  # content 分片大小（字符）


# ===================================================================
# 内部辅助
# ===================================================================

def _sse(event: str, data: dict[str, Any]) -> str:
    """构建单条 SSE 事件字符串。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_final_result(
    result: dict[str, Any],
    demand_dict: dict[str, Any],
) -> dict[str, Any]:
    """100% 复用 src/api/main.py:_extract_final_result 逻辑。

    从 Agent 结果中提取 final_result，兜底手动组装；校验失败时附加 issues 到 tips。
    """
    outline: dict[str, Any] = result.get("travel_outline", {})
    final_result: dict[str, Any] = outline.get("final_result", {})

    if not final_result:
        final_result = {
            "destination": demand_dict.get("destination", ""),
            "total_days": len(result.get("daily_plans", [])),
            "total_budget": demand_dict.get("total_budget", 0),
            "people": demand_dict.get("people", ""),
            "preferences": demand_dict.get("preferences", []),
            "daily_plans": result.get("daily_plans", []),
            "travel_tips": [],
            "iteration_count": result.get("iteration_count", 0),
            "check_result": result.get("check_result", {}),
        }

    # 校验失败时，将 issues 附加到 travel_tips
    check_result: dict[str, Any] = final_result.get("check_result", {})
    iteration_count: int = final_result.get("iteration_count", 0)
    if not check_result.get("is_pass", True) and iteration_count >= 3:
        issues: list[str] = check_result.get("issues", [])
        if issues:
            existing_tips: list[str] = final_result.get("travel_tips", [])
            existing_tips.append(
                f"⚠️ 行程经过 {iteration_count} 轮优化仍存在以下问题，建议手动微调："
            )
            for issue in issues[:5]:
                existing_tips.append(f"  • {issue}")
            final_result["travel_tips"] = existing_tips

    react_trace = result.get("react_trace", [])
    if react_trace:
        final_result["react_trace"] = react_trace
    final_result["run_mode"] = result.get("run_mode", "react")

    return final_result


async def _stream_markdown(result: dict[str, Any], demand: dict[str, Any]):
    """将行程结果渲染为 Markdown 后逐段推送 content 事件。"""
    try:
        export_svc = get_export_service()
        outline = result.get("travel_outline", {})
        travel_data: dict[str, Any] = outline.get("final_result", {})
        if not travel_data:
            travel_data = _extract_final_result(result, demand)

        md_bytes: bytes = export_svc.export_markdown(travel_data)
        md_text: str = md_bytes.decode("utf-8")

        for i in range(0, len(md_text), _MARKDOWN_CHUNK_SIZE):
            fragment: str = md_text[i:i + _MARKDOWN_CHUNK_SIZE]
            yield _sse("content", {"fragment": fragment})
            await asyncio.sleep(0)  # 让出事件循环，确保浏览器及时渲染
    except Exception as exc:
        logger.exception("Markdown 渲染/推送异常")
        yield _sse("error", {"code": 500, "message": f"结果渲染失败: {str(exc)}"})


async def _mark_task_success(task_id: str, result: dict[str, Any]) -> None:
    """将任务标记为成功 —— 写入原有任务库，兼容查询/导出。"""
    try:
        task_svc = await get_task_service()
        await task_svc.complete_immediately(task_id, result)
        logger.info("SSE流式任务写入成功: task_id=%s", task_id)
    except Exception as exc:
        logger.error("SSE流式任务写入成功失败（非致命）: task_id=%s, error=%s",
                     task_id, exc)


async def _mark_task_failed(task_id: str, error: str) -> None:
    """将任务标记为失败，写入错误信息。"""
    try:
        task_svc = await get_task_service()
        # cancel 仅处理 PENDING/RUNNING → 直接覆写状态与错误信息
        async with task_svc._lock:
            t = task_svc._tasks.get(task_id)
            if t is not None:
                t.status = TaskStatus.FAILED
                t.error = error[:500]
                t.progress = ""
        logger.info("SSE流式任务标记失败: task_id=%s, error=%s",
                     task_id, error[:200])
    except Exception as exc:
        logger.error("SSE流式任务标记失败异常（非致命）: task_id=%s, error=%s",
                     task_id, exc)


async def _update_task_progress(task_id: str, progress: str) -> None:
    """更新任务进度描述（非关键路径，失败忽略）。"""
    try:
        task_svc = await get_task_service()
        async with task_svc._lock:
            t = task_svc._tasks.get(task_id)
            if t is not None:
                t.progress = progress
    except Exception:
        pass


# ===================================================================
# 核心流式生成协程
# ===================================================================

async def _generate_stream(
    demand_dict: dict[str, Any],
    task_id: str,
) -> AsyncGenerator[str, None]:
    """运行 LangGraph 工作流，逐节点推送 progress + 最终推送 content/done/error。

    完全复用现有节点链路：demand_analyze → spot_retrieve →
    (outline_generate ‖ spot_pre_check) → daily_fill → fact_check →
    plan_check → (react_revise ⇄ daily_fill) → result_summary。
    所有校验、预算管控、降级兜底逻辑完整保留。
    """
    cache = get_cache_service()

    try:
        # ================================================================
        # 1. 参数校验（复用 run_travel_planner 中的 _validate_demand_params）
        # ================================================================
        from src.agent import _validate_demand_params

        validation_error = _validate_demand_params(demand_dict)
        if validation_error:
            yield _sse("error", {
                "code": 400,
                "message": f"参数校验失败: {validation_error}",
            })
            await _mark_task_failed(task_id, f"参数校验失败: {validation_error}")
            return

        await _update_task_progress(task_id, "正在检查缓存…")

        # ================================================================
        # 2. 缓存检查（100% 复用 _execute_and_cache 的缓存逻辑）
        # ================================================================
        cached = cache.get(demand_dict)
        if cached is not None:
            logger.info("SSE流式请求缓存命中: task_id=%s", task_id)
            yield _sse("progress", {
                "node": "cache",
                "step": "cache_hit",
                "percent": 10,
                "description": "缓存命中，直接返回结果",
            })
            async for event in _stream_markdown(cached, demand_dict):
                yield event
            yield _sse("done", {"task_id": task_id})
            await _mark_task_success(task_id, cached)
            return

        # ================================================================
        # 3. 构建初始状态（与 run_travel_planner 完全一致）
        # ================================================================
        days = max(1, int(demand_dict.get("days", 1)))
        total_budget = max(0.0, float(demand_dict.get("total_budget", 0)))
        preferences = demand_dict.get("preferences", [])
        if not isinstance(preferences, list):
            preferences = []

        initial_state: dict[str, Any] = {
            "user_demand": {
                "destination": str(demand_dict.get("destination", "")),
                "days": days,
                "total_budget": total_budget,
                "people": str(demand_dict.get("people", "")),
                "preferences": preferences,
                "remark": str(demand_dict.get("remark", "")),
            },
            "travel_outline": {},
            "daily_plans": [],
            "check_result": {},
            "iteration_count": 0,
            "current_step": "",
            "error_msg": "",
            "react_trace": [],
            "run_mode": os.getenv("TRAVEL_PLANNER_MODE", "react").strip().lower(),
            "revision_round": 0,
            "revision_instruction": "",
            "revision_history": [],
        }

        await _update_task_progress(task_id, "正在生成行程…")

        # ================================================================
        # 4. 流式执行 LangGraph（astream 在每节点完成时 yield 增量状态）
        #    100% 复用全部节点逻辑、校验、预算管控、降级兜底
        # ================================================================
        accumulated_state: dict[str, Any] = {}
        node_completed: set[str] = set()

        async for chunk in travel_planner_graph.astream(
            initial_state,
            stream_mode="updates",
        ):
            # chunk: {node_name: state_update}，并行节点可能同帧出现
            for node_name, state_update in chunk.items():
                accumulated_state.update(state_update)

                step = state_update.get("current_step", "")
                error_msg = state_update.get("error_msg", "")

                # ---- 错误中断 ----
                if step == "error" or error_msg:
                    yield _sse("error", {
                        "code": 500,
                        "message": error_msg or f"节点 {node_name} 执行异常",
                        "node": node_name,
                    })
                    await _mark_task_failed(
                        task_id,
                        error_msg or f"节点 {node_name} 执行异常",
                    )
                    return

                # ---- 推送 progress 事件（每个节点首次完成时） ----
                if node_name not in node_completed:
                    node_completed.add(node_name)
                    info = _NODE_PROGRESS.get(node_name)
                    if info:
                        pct, st, desc = info
                        yield _sse("progress", {
                            "node": node_name,
                            "step": st,
                            "percent": pct,
                            "description": desc,
                        })
                        await _update_task_progress(task_id, desc)

        # ================================================================
        # 5. 出口守卫（与 run_travel_planner 完全一致的校验逻辑）
        # ================================================================
        result = dict(accumulated_state)
        daily_plans = result.get("daily_plans", [])
        error_msg = result.get("error_msg", "")
        current_step = result.get("current_step", "")

        if not error_msg and current_step != "error":
            if not daily_plans or len(daily_plans) == 0:
                result["error_msg"] = (
                    "行程生成失败: 未产生任何有效的每日行程计划，请重试"
                )
            elif not any(
                len(day.get("spots", [])) > 0
                and any(s.get("name", "").strip() for s in day.get("spots", []))
                for day in daily_plans
            ):
                result["error_msg"] = (
                    "行程生成失败: 每日计划中无有效景点数据，"
                    "请检查目的地是否有足够景点"
                )

        final_error = result.get("error_msg", "")
        if final_error:
            logger.error("SSE流式生成出口守卫拦截: %s", final_error)
            yield _sse("error", {"code": 500, "message": final_error})
            await _mark_task_failed(task_id, final_error)
            return

        # ================================================================
        # 6. 写入缓存（复用 _execute_and_cache 逻辑）
        # ================================================================
        cache.set(demand_dict, result)

        # ================================================================
        # 7. 流式推送 Markdown 正文（content 事件）
        # ================================================================
        async for event in _stream_markdown(result, demand_dict):
            yield event

        # ================================================================
        # 8. 完成 —— 推送 done 事件 + 写入任务库（兼容查询/导出）
        # ================================================================
        yield _sse("done", {"task_id": task_id})
        await _mark_task_success(task_id, result)

    except Exception as exc:
        logger.exception("SSE流式生成异常: task_id=%s", task_id)
        yield _sse("error", {
            "code": 500,
            "message": f"服务内部错误: {str(exc)}",
        })
        await _mark_task_failed(task_id, str(exc))


# ===================================================================
# POST /api/travel/generate-stream
# ===================================================================

@router.post(
    "/api/travel/generate-stream",
    summary="生成旅行行程规划（SSE流式）",
    description=(
        "提交出行需求，通过 Server-Sent Events 流式推送行程生成进度与结果。\n\n"
        "**入参**：与 `/api/travel/generate-async` 完全一致。\n\n"
        "**事件格式**（4 类，前端可直接用 EventSource 解析）：\n"
        "- `progress`：推送当前执行节点、进度百分比、状态说明\n"
        "- `content`：逐段推送生成的 Markdown 行程正文片段\n"
        "- `done`：推送最终 task_id，标识生成结束\n"
        "- `error`：推送错误信息，正常关闭连接\n\n"
        "生成完成后的任务可通过 `GET /api/travel/task/{task_id}` "
        "查询状态，通过 `GET /api/travel/export/{task_id}` 导出行程文件。\n\n"
        "**异常处理**：大模型调用失败、参数异常均推送 error 事件，服务不崩溃。"
    ),
)
async def generate_travel_stream(request: Request):
    """SSE 流式行程生成端点。

    流程：
    1. 手动解析请求体并校验（参数异常时推送 SSE error 事件而非 HTTP 422）
    2. 创建任务条目（写入原有任务库）
    3. 启动 LangGraph 工作流，逐节点通过 SSE 推送进度
    4. 生成完成后逐段推送 Markdown 正文
    5. 最终推送 done 事件 + 标记任务成功
    6. 异常时推送 error 事件 + 标记任务失败，连接正常关闭
    """
    # ---- 手动解析请求体，使参数校验错误也走 SSE error 事件 ----
    try:
        body = await request.json()
    except Exception:
        # 请求体非 JSON
        async def _bad_body():
            yield _sse("error", {"code": 400, "message": "请求体格式错误，请使用 JSON 格式"})
        return StreamingResponse(
            _bad_body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # Pydantic 校验（参数不合法 → SSE error，而非 HTTP 422）
    try:
        validated = TravelPlanRequest.model_validate(body)
        demand_dict: dict[str, Any] = validated.model_dump()
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = " → ".join(str(p) for p in err.get("loc", []))
            errors.append(f"{loc}: {err.get('msg', '')}")
        error_msg = "参数校验失败: " + "; ".join(errors[:3])

        async def _validation_error_stream():
            yield _sse("error", {"code": 400, "message": error_msg})
        return StreamingResponse(
            _validation_error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # 创建任务条目（复用 submit 创建的 task_id，后续 complete_immediately 覆写结果）
    task_svc = await get_task_service()
    task_id: str = await task_svc.submit(lambda d: d, demand_dict, timeout=600)

    logger.info(
        "SSE流式请求开始: task_id=%s, destination=%s, days=%s",
        task_id,
        demand_dict.get("destination", ""),
        demand_dict.get("days", ""),
    )

    return StreamingResponse(
        _generate_stream(demand_dict, task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 代理缓冲
        },
    )
