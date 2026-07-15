"""
FastAPI 主程序 —— 旅行行程规划 API 服务。

启动方式:
    uvicorn src.api.main:app --reload --port 8000

接口文档:
    Swagger UI:  http://localhost:8000/docs
    ReDoc:       http://localhost:8000/redoc

接口列表:
    GET  /health                      健康检查
    POST /api/travel/generate         同步生成行程（含缓存）
    POST /api/travel/generate-async   异步提交行程任务
    GET  /api/travel/task/{task_id}   查询异步任务状态
    POST /api/travel/revise           同步修改行程（增量）
    POST /api/travel/revise-async     异步提交行程修改任务
    GET  /api/travel/export/{task_id} 导出行程文件
    POST /api/travel/export           直接导出行程（无需 task_id）
    GET  /api/travel/cache/stats      缓存统计
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from io import BytesIO

from src.agent import revise_travel_plan, run_travel_planner
from src.api.schemas import (
    ApiResponse,
    AsyncTaskResponse,
    ExportRequest,
    ReviseRequest,
    TaskStatusResponse,
    TravelPlanRequest,
)
from src.api.stream_endpoint import router as stream_router
from src.services.cache_service import get_cache_service
from src.services.export_service import _build_export_filename, get_export_service

# ---------------------------------------------------------------
# 内部辅助：构造安全的 Content-Disposition filename header
# ---------------------------------------------------------------
def _make_content_disposition(filename: str) -> str:
    """构造 RFC 5987 兼容的 Content-Disposition header。

    支持中文等非 ASCII 字符：使用 filename*=UTF-8''url_encoded 格式，
    同时提供 ASCII fallback 的 filename= 字段（供老旧浏览器使用）。
    """
    from urllib.parse import quote

    encoded: str = quote(filename, safe="")

    # ASCII fallback: 提取扩展名 + 使用可读的英文 fallback
    ext: str = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1]

    # 尝试提取 ASCII 部分作为 fallback
    ascii_part: str = "".join(
        c for c in filename if ord(c) < 128 and c not in '\\/:*?"<>|'
    ).strip()

    # 如果 ASCII 部分太短（如只有数字），使用完整描述性名称
    if len(ascii_part.replace(ext, "").strip()) < 4:
        ascii_fallback = f"travel_plan{ext}"
    else:
        ascii_fallback = ascii_part

    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'
from src.services.task_service import (
    TaskNotFoundError,
    TaskStatus,
    get_task_service,
)

logger = logging.getLogger(__name__)

# ============================================================
# 服务初始化
# ============================================================
cache = get_cache_service()
# task_service 是异步单例，通过 lifespan 初始化

# ============================================================
# FastAPI 实例初始化
# ============================================================
app = FastAPI(
    title="旅行行程规划 API",
    version="2.0.0",
    description=(
        "基于 LangGraph Agent 的智能旅行行程规划服务。\n\n"
        "## 功能\n"
        "- 输入目的地、天数、预算、人群、偏好，自动生成多日行程\n"
        "- 内置景点检索、预算校验、天气查询等工具链\n"
        "- **ReAct 自主修正**：校验不通过时 LLM 自主调用工具定向修正（默认启用）\n"
        "- **结果缓存**：相同参数命中缓存直接返回，大幅降低响应耗时\n"
        "- **异步任务**：支持异步提交 + 轮询查询，适配长时间生成场景\n"
        "- **增量修订**：支持自然语言对话式修改，最多 5 轮连续调整\n"
        "- **行程导出**：支持 Markdown / PDF 格式导出，一键下载行程文件\n"
        "- 支持最多 3 轮迭代优化，确保行程合理可用\n\n"
        "## 运行模式\n"
        "- `TRAVEL_PLANNER_MODE=react`（默认）：ReAct 自主修正\n"
        "- `TRAVEL_PLANNER_MODE=classic`：原有固定流程\n"
        "- `TRAVEL_CACHE_ENABLED=true/false`：缓存开关\n\n"
        "## 技术栈\n"
        "- **框架**: FastAPI + Pydantic v2\n"
        "- **AI Agent**: LangGraph StateGraph + ReAct ToolNode\n"
        "- **大模型**: OpenAI-compatible API\n"
        "- **向量检索**: Chroma + BGE-small-zh"
    ),
    contact={
        "name": "Travel Planner Team",
    },
    license_info={
        "name": "MIT",
    },
)


# ============================================================
# Lifespan —— 应用启停钩子
# ============================================================
@app.on_event("startup")
async def startup_event() -> None:
    """应用启动时预热服务（task_service 单例初始化）。"""
    _ = await get_task_service()
    logger.info("应用已启动，缓存状态: enabled=%s, size=%d",
                 cache.enabled, cache.size)


# ============================================================
# CORS 跨域配置 —— 全量放开，方便前端对接
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 SSE 流式接口路由
app.include_router(stream_router)

# ============================================================
# 全局异常处理器
# ============================================================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """捕获所有未处理异常，返回统一格式错误响应，不暴露堆栈。"""
    return JSONResponse(
        status_code=500,
        content=ApiResponse(
            code=500,
            msg=f"服务器内部错误: {str(exc)}",
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """将 Pydantic 校验错误转为统一 ApiResponse 格式。

    提取 first error 的字段名 + 原因作为人类可读消息，
    同时返回完整 errors 列表供开发者调试。
    """
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        loc: str = " → ".join(str(p) for p in err.get("loc", []))
        errors.append({
            "field": loc,
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        })

    # 构造人类可读的摘要消息
    if errors:
        first = errors[0]
        summary = f"参数校验失败: {first['field']} — {first['message']}"
        if len(errors) > 1:
            summary += f"（共 {len(errors)} 项错误）"
    else:
        summary = "参数校验失败，请检查请求体格式"

    logger.warning(
        "请求参数校验失败: path=%s, errors=%s",
        request.url.path, errors,
    )

    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "msg": summary,
            "errors": errors,
        },
    )


# ============================================================
# 内部辅助
# ============================================================
def _execute_and_cache(demand_dict: dict[str, Any]) -> dict[str, Any]:
    """执行 Agent 流程，带缓存检查与写入。"""
    # 1) 查缓存
    cached = cache.get(demand_dict)
    if cached is not None:
        logger.info("缓存命中，跳过 Agent 执行")
        return cached

    # 2) 未命中 → 执行 Agent
    result: dict[str, Any] = run_travel_planner(demand_dict)

    # 3) 写入缓存
    cache.set(demand_dict, result)
    return result


def _extract_final_result(
    result: dict[str, Any],
    demand_dict: dict[str, Any],
) -> dict[str, Any]:
    """从 Agent 结果中提取 final_result，兜底手动组装。"""
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

    # ---- 校验失败时，将 issues 附加到 travel_tips 提示用户 ----
    check_result: dict[str, Any] = final_result.get("check_result", {})
    iteration_count: int = final_result.get("iteration_count", 0)
    if not check_result.get("is_pass", True) and iteration_count >= 3:
        issues: list[str] = check_result.get("issues", [])
        if issues:
            existing_tips: list[str] = final_result.get("travel_tips", [])
            existing_tips.append(
                f"⚠️ 行程经过 {iteration_count} 轮优化仍存在以下问题，建议手动微调："
            )
            for issue in issues[:5]:  # 最多展示 5 条
                existing_tips.append(f"  • {issue}")
            final_result["travel_tips"] = existing_tips

    # 附加 ReAct 追踪信息（如有）
    react_trace = result.get("react_trace", [])
    if react_trace:
        final_result["react_trace"] = react_trace
    final_result["run_mode"] = result.get("run_mode", "react")

    # 附加修订元数据（如有）
    revision_round = result.get("revision_round", 0)
    if revision_round > 0:
        final_result["revision_round"] = revision_round
        final_result["revision_instruction"] = result.get("revision_instruction", "")
        final_result["revision_history"] = result.get("revision_history", [])
        revision_diff = result.get("revision_diff")
        if revision_diff:
            final_result["revision_diff"] = revision_diff

    return final_result


# ============================================================
# POST /api/travel/revise-async —— 异步行程修改
# ============================================================
@app.post(
    "/api/travel/revise-async",
    response_model=AsyncTaskResponse,
    summary="对话式修改行程（异步）",
    description=(
        "异步提交行程修改任务，立即返回任务 ID。\n"
        "后续通过 GET /api/travel/task/{task_id} 轮询查询状态与结果。\n\n"
        "仅支持 task_id 模式（需基于已完成的任务进行修改）。\n\n"
        "任务状态流转: pending → running → success/failed"
    ),
    tags=["Travel"],
)
async def revise_travel_async(request: ReviseRequest) -> AsyncTaskResponse:
    """异步提交行程修改任务。

    基于已完成的任务 ID，异步执行增量修改。
    """
    try:
        original_result, instruction = await _resolve_revise_params(request)

        # 包装为异步执行函数
        def _run_revise() -> dict[str, Any]:
            return revise_travel_plan(
                original_result=original_result,
                instruction=instruction,
            )

        task_service = await get_task_service()
        task_id = await task_service.submit(_run_revise)

        return AsyncTaskResponse(
            code=200,
            msg="修改任务已提交，请轮询查询结果",
            task_id=task_id,
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("异步行程修改提交异常")
        raise HTTPException(status_code=500, detail=f"服务内部错误: {str(exc)}")


# ============================================================
# GET /api/travel/export/{task_id} —— 导出行程
# ============================================================
@app.get(
    "/api/travel/export/{task_id}",
    summary="导出行程文件（通过任务 ID）",
    description=(
        "根据任务 ID 导出对应行程为 Markdown 或 PDF 文件。\n\n"
        "- `format=md`: 导出 Markdown 格式（默认），UTF-8 文本\n"
        "- `format=pdf`: 导出 PDF 格式（需安装 weasyprint 或 pdfkit）\n\n"
        "文件包含：行程概览 → 每日详情（景点表格 + 餐饮 + 交通） → 注意事项 → 修订变更摘要。\n\n"
        "导出依赖完整链路追踪，请求/格式/耗时/数据量全可追溯。\n\n"
        "**适用场景**：异步生成/修订后的行程（有 task_id）。同步生成/修订的场景请使用 POST /api/travel/export。"
    ),
    tags=["Travel"],
)
async def export_travel_plan(
    task_id: str,
    format: str = Query("md", pattern="^(md|pdf)$", description="导出格式: md 或 pdf"),
) -> Response:
    """导出行程为指定格式文件。

    Args:
        task_id: 任务 ID（最小 1 字符）。
        format: 导出格式（md 或 pdf），默认 md。
    """
    # 参数校验：task_id 不能为空
    task_id = (task_id or "").strip()
    if not task_id:
        raise HTTPException(
            status_code=400,
            detail="缺少必填参数: task_id 不能为空",
        )

    try:
        # 获取任务结果
        task_service = await get_task_service()
        try:
            task = await task_service.get(task_id)
        except TaskNotFoundError:
            logger.warning("导出失败: 任务不存在 task_id=%s", task_id)
            raise HTTPException(
                status_code=404,
                detail=f"任务不存在: {task_id}，请检查任务 ID 是否正确",
            )

        # 状态校验：非成功状态不允许导出
        if task.status != TaskStatus.SUCCESS:
            logger.warning(
                "导出失败: 任务未完成 task_id=%s, status=%s",
                task_id, task.status.value,
            )
            raise HTTPException(
                status_code=400,
                detail=f"任务尚未完成（状态: {task.status.value}），请等待任务成功后再导出",
            )

        if not task.result:
            logger.warning(
                "导出失败: 任务结果为空 task_id=%s, status=%s",
                task_id, task.status.value,
            )
            raise HTTPException(
                status_code=400,
                detail="任务结果为空，无法导出。请重新提交行程生成任务",
            )

        # 提取行程数据 — 兼容多种数据结构
        result: dict[str, Any] = task.result
        outline: dict[str, Any] = result.get("travel_outline", {}) or {}
        travel_data: dict[str, Any] = outline.get("final_result", {}) or {}

        if not travel_data:
            # 尝试从 result 直接提取（兼容旧版或简化后的结果）
            user_demand: dict[str, Any] = result.get("user_demand", {}) or {}
            # travel_tips 在 travel_outline.final_result 内部，不在 TravelState 顶层
            existing_tips = (
                outline.get("final_result", {}).get("travel_tips", [])
                if isinstance(outline, dict) else []
            )
            travel_data = {
                "destination": user_demand.get("destination", ""),
                "total_days": len(result.get("daily_plans", [])),
                "total_budget": user_demand.get("total_budget", 0),
                "people": user_demand.get("people", ""),
                "preferences": user_demand.get("preferences", []),
                "daily_plans": result.get("daily_plans", []),
                "travel_tips": existing_tips,
                "check_result": result.get("check_result", {}),
                "iteration_count": result.get("iteration_count", 0),
                "run_mode": result.get("run_mode", "react"),
                "revision_round": result.get("revision_round", 0),
                "revision_diff": result.get("revision_diff"),
            }

        # ---- 内容校验：确认 daily_plans 包含有效行程数据 ----
        daily_plans: list[dict[str, Any]] = travel_data.get("daily_plans", [])
        has_content = (
            isinstance(daily_plans, list)
            and len(daily_plans) > 0
            and any(
                isinstance(day, dict)
                and len(day.get("spots", [])) > 0
                and any(
                    isinstance(s, dict) and str(s.get("name", "")).strip()
                    for s in day.get("spots", [])
                )
                for day in daily_plans
            )
        )

        if not has_content:
            logger.warning(
                "导出失败: 行程内容为空 task_id=%s, daily_plans_len=%d, "
                "destination=%s",
                task_id,
                len(daily_plans) if isinstance(daily_plans, list) else 0,
                travel_data.get("destination", ""),
            )
            raise HTTPException(
                status_code=400,
                detail="行程内容为空，无法导出。请重新提交行程生成任务",
            )

        export_service = get_export_service()

        # 构造动态文件名：{目的地}{天数}天行程规划.{后缀}
        destination: str = str(travel_data.get("destination", "") or "行程").strip()
        total_days: int = int(travel_data.get("total_days", 0) or 0)

        if format == "md":
            md_bytes: bytes = export_service.export_markdown(travel_data)
            filename: str = _build_export_filename(destination, "md", total_days)
            logger.info(
                "Markdown 导出成功: task_id=%s, size=%d bytes, filename=%s",
                task_id, len(md_bytes), filename,
            )
            return Response(
                content=md_bytes,
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": _make_content_disposition(filename),
                    "Content-Type": "text/markdown; charset=utf-8",
                },
            )
        else:  # pdf
            pdf_bytes: bytes = export_service.export_pdf(travel_data)
            filename = _build_export_filename(destination, "pdf", total_days)
            logger.info(
                "PDF 导出成功: task_id=%s, size=%d bytes, filename=%s",
                task_id, len(pdf_bytes), filename,
            )
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": _make_content_disposition(filename),
                    "Content-Type": "application/pdf",
                },
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("导出异常: task_id=%s, format=%s", task_id, format)
        raise HTTPException(
            status_code=500,
            detail=f"导出服务内部错误: {str(exc)}",
        )


# ============================================================
# POST /api/travel/export —— 直接导出行程（无需 task_id）
# ============================================================
@app.post(
    "/api/travel/export",
    summary="直接导出行程文件（通过行程数据）",
    description=(
        "直接传入行程数据导出为 Markdown 或 PDF 文件。\n\n"
        "适用于同步生成或修订后的行程（无 task_id 时使用）。\n\n"
        "- `format=md`: 导出 Markdown 格式（默认），UTF-8 文本\n"
        "- `format=pdf`: 导出 PDF 格式\n\n"
        "文件内容：行程概览表格 → 每日详情（景点 + 餐饮 + 交通） → 出行注意事项 → 修订变更。\n\n"
        "导出请求全链路追踪，格式/数据量/耗时全可追溯。"
    ),
    tags=["Travel"],
)
async def export_travel_plan_direct(request: ExportRequest) -> Response:
    """直接导出行程数据为指定格式文件。

    Args:
        request: 包含 travel_data 和 format。
    """
    try:
        travel_data: dict[str, Any] = request.travel_data
        if not travel_data:
            logger.warning("直接导出失败: travel_data 为空")
            raise HTTPException(
                status_code=400,
                detail="行程数据为空，无法导出",
            )

        # ---- 内容校验：确认 daily_plans 包含有效行程数据 ----
        daily_plans: list[dict[str, Any]] = travel_data.get("daily_plans", [])
        has_content = (
            isinstance(daily_plans, list)
            and len(daily_plans) > 0
            and any(
                isinstance(day, dict)
                and len(day.get("spots", [])) > 0
                and any(
                    isinstance(s, dict) and str(s.get("name", "")).strip()
                    for s in day.get("spots", [])
                )
                for day in daily_plans
            )
        )

        if not has_content:
            logger.warning(
                "直接导出失败: 行程内容为空 destination=%s, daily_plans_len=%d",
                travel_data.get("destination", ""),
                len(daily_plans) if isinstance(daily_plans, list) else 0,
            )
            raise HTTPException(
                status_code=400,
                detail="行程内容为空，无法导出。请检查传入的行程数据是否完整",
            )

        export_service = get_export_service()

        # 构造动态文件名：{目的地}{天数}天行程规划.{后缀}
        destination: str = str(travel_data.get("destination", "") or "行程").strip()
        total_days: int = int(travel_data.get("total_days", 0) or 0)

        if request.format == "md":
            md_bytes: bytes = export_service.export_markdown(travel_data)
            filename: str = _build_export_filename(destination, "md", total_days)
            logger.info(
                "直接 Markdown 导出成功: destination=%s, size=%d bytes, filename=%s",
                destination, len(md_bytes), filename,
            )
            return Response(
                content=md_bytes,
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": _make_content_disposition(filename),
                    "Content-Type": "text/markdown; charset=utf-8",
                },
            )
        else:  # pdf
            pdf_bytes: bytes = export_service.export_pdf(travel_data)
            filename = _build_export_filename(destination, "pdf", total_days)
            logger.info(
                "直接 PDF 导出成功: destination=%s, size=%d bytes, filename=%s",
                destination, len(pdf_bytes), filename,
            )
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": _make_content_disposition(filename),
                    "Content-Type": "application/pdf",
                },
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("直接导出异常: format=%s", request.format)
        raise HTTPException(
            status_code=500,
            detail=f"导出服务内部错误: {str(exc)}",
        )


# ============================================================
# GET /health —— 健康检查
# ============================================================
@app.get(
    "/health",
    summary="健康检查",
    description="返回服务运行状态、缓存统计。",
    tags=["System"],
)
async def health_check() -> dict[str, Any]:
    """服务探活接口，含缓存统计信息。"""
    return {
        "status": "healthy",
        "service": "travel-planner",
        "version": "2.0.0",
        "cache": cache.stats,
    }


# ============================================================
# 内部辅助：解析 ReviseRequest → (original_result, instruction)
# ============================================================
async def _resolve_revise_params(request: ReviseRequest) -> tuple[dict[str, Any], str]:
    """从 ReviseRequest 中解析出原始行程数据和修改指令。

    支持两种模式：
    1. task_id 模式：通过 task_id 从任务服务查询原始行程。
    2. original_data 模式：直接使用传入的行程数据。

    Returns:
        (original_result, instruction)

    Raises:
        ValueError: 参数不合法（缺必填字段、task_id 不存在等）。
    """
    # ---- 解析修改指令 ----
    instruction: str = (request.modify_prompt or request.instruction or "").strip()
    if not instruction:
        raise ValueError("缺少修改指令，请填写 modify_prompt 或 instruction 字段。")

    # ---- 解析原始行程 ----
    if request.task_id:
        # task_id 模式：从任务服务查询
        task_service = await get_task_service()
        try:
            task = await task_service.get(request.task_id)
        except Exception:
            raise ValueError(f"任务不存在: {request.task_id}")

        if task.status != TaskStatus.SUCCESS or not task.result:
            raise ValueError(
                f"任务 {request.task_id} 尚未完成（状态: {task.status.value}），"
                "请等待任务完成后再发起修改。"
            )

        original_result: dict[str, Any] = task.result
    elif request.original_data:
        # original_data 模式：直接使用
        original_result = request.original_data
    else:
        raise ValueError("缺少原始行程数据，请填写 task_id 或 original_data 字段。")

    return original_result, instruction


# ============================================================
# POST /api/travel/revise —— 对话式行程修改（同步）
# ============================================================
@app.post(
    "/api/travel/revise",
    response_model=ApiResponse,
    summary="对话式修改行程（增量、同步）",
    description=(
        "基于已生成的行程，输入自然语言修改指令进行定向调整。\n\n"
        "**只修改指令涉及的天数/内容，未提及的行程保持原样。**\n\n"
        "支持两种传参方式：\n"
        "1. `task_id` + `modify_prompt`（推荐）：通过异步任务 ID 定位原始行程\n"
        "2. `original_data` + `instruction`（兼容旧版）：直接传入完整行程数据\n\n"
        "流程：解析修改意图 → 定向调整目标天数 → 校验 → ReAct 自主修正 → 汇总\n\n"
        "最多支持 5 轮连续对话式调整，每轮基于上一轮结果叠加修改。"
    ),
    tags=["Travel"],
)
async def revise_travel_plan_endpoint(request: ReviseRequest) -> ApiResponse:
    """对话式行程修改 —— 增量调整已生成的行程。

    Args:
        request: 包含 task_id/modify_prompt 或 original_data/instruction。
    """
    try:
        original_result, instruction = await _resolve_revise_params(request)

        # 执行增量修订
        result: dict[str, Any] = revise_travel_plan(
            original_result=original_result,
            instruction=instruction,
        )

        # 检查错误
        error_msg: str = result.get("error_msg", "")
        if error_msg:
            return ApiResponse(
                code=500,
                msg=f"行程修改失败: {error_msg}",
            )

        # 提取最终结果
        outline: dict[str, Any] = result.get("travel_outline", {})
        final_result: dict[str, Any] = outline.get("final_result", {})

        if not final_result:
            demand = result.get("user_demand", {})
            final_result = {
                "destination": demand.get("destination", ""),
                "total_days": len(result.get("daily_plans", [])),
                "total_budget": demand.get("total_budget", 0),
                "people": demand.get("people", ""),
                "preferences": demand.get("preferences", []),
                "daily_plans": result.get("daily_plans", []),
                "travel_tips": [],
                "iteration_count": result.get("iteration_count", 0),
                "check_result": result.get("check_result", {}),
            }

        # 附加修订信息
        react_trace = result.get("react_trace", [])
        if react_trace:
            final_result["react_trace"] = react_trace
        final_result["run_mode"] = result.get("run_mode", "react")
        final_result["revision_round"] = result.get("revision_round", 0)
        final_result["revision_history"] = result.get("revision_history", [])

        return ApiResponse(
            code=200,
            msg=f"行程修改成功（第 {result.get('revision_round', 0)} 轮修订）",
            data=final_result,
        )

    except ValueError as exc:
        return ApiResponse(code=400, msg=f"参数错误: {str(exc)}")
    except Exception as exc:
        logger.exception("行程修改异常")
        return ApiResponse(code=500, msg=f"服务内部错误: {str(exc)}")


# ============================================================
# GET /api/travel/cache/stats —— 缓存统计
# ============================================================
@app.get(
    "/api/travel/cache/stats",
    summary="缓存统计",
    description="查看当前缓存命中率、条目数等统计信息。",
    tags=["Travel"],
)
async def cache_stats() -> dict[str, Any]:
    """查询缓存统计。"""
    return cache.stats


# ============================================================
# POST /api/travel/generate —— 同步行程生成（保留 100% 兼容）
# ============================================================
@app.post(
    "/api/travel/generate",
    response_model=ApiResponse,
    summary="生成旅行行程规划（同步）",
    description=(
        "提交出行需求，由 AI Agent 自动规划多日行程。\n\n"
        "流程：需求解析 → 景点检索 → 框架生成 → 每日填充 → "
        "校验 → (ReAct 自主修正 ⇄ 填充) → 汇总输出\n\n"
        "相同参数会被缓存，命中缓存时直接返回无需等待。"
    ),
    tags=["Travel"],
)
async def generate_travel_plan(request: TravelPlanRequest) -> ApiResponse:
    """同步生成旅行行程规划。

    将请求参数转为 Agent 所需字典，调用 run_travel_planner 执行全流程，
    提取最终结果后通过 ApiResponse 统一返回。
    自动检查缓存，命中时直接返回。
    """
    try:
        demand_dict: dict[str, Any] = request.model_dump()

        # 执行 Agent（含缓存检查）
        result: dict[str, Any] = _execute_and_cache(demand_dict)

        # 检查 Agent 是否报错
        error_msg: str = result.get("error_msg", "")
        if error_msg:
            return ApiResponse(
                code=500,
                msg=f"行程规划执行失败: {error_msg}",
            )

        final_result = _extract_final_result(result, demand_dict)

        return ApiResponse(
            code=200,
            msg="success",
            data=final_result,
        )

    except ValueError as exc:
        return ApiResponse(code=500, msg=f"配置错误: {str(exc)}")
    except Exception as exc:
        return ApiResponse(code=500, msg=f"服务内部错误: {str(exc)}")


# ============================================================
# POST /api/travel/generate-async —— 异步行程生成
# ============================================================
@app.post(
    "/api/travel/generate-async",
    response_model=AsyncTaskResponse,
    summary="生成旅行行程规划（异步）",
    description=(
        "异步提交出行需求，立即返回任务 ID。\n"
        "后续通过 GET /api/travel/task/{task_id} 轮询查询状态与结果。\n\n"
        "任务状态流转: pending → running → success/failed"
    ),
    tags=["Travel"],
)
async def generate_travel_async(request: TravelPlanRequest) -> AsyncTaskResponse:
    """异步提交行程生成任务，返回任务 ID 供轮询。

    异步任务也会经过缓存检查：如果缓存命中则直接标记为成功。
    """
    try:
        demand_dict: dict[str, Any] = request.model_dump()

        # 先查缓存 —— 命中则直接完成，无需入队
        cached = cache.get(demand_dict)
        if cached is not None:
            logger.info("异步请求缓存命中，直接返回")
            task_service = await get_task_service()
            task_id = await task_service.submit(
                lambda d: d, demand_dict,
            )
            # 通过公开 API 直接将缓存结果写入
            await task_service.complete_immediately(task_id, cached)
            return AsyncTaskResponse(
                code=200,
                msg="缓存命中，任务已完成",
                task_id=task_id,
            )

        # 缓存未命中 → 包装为带缓存的执行函数
        def _run_with_cache(d: dict[str, Any]) -> dict[str, Any]:
            return _execute_and_cache(d)

        task_service = await get_task_service()
        task_id = await task_service.submit(
            _run_with_cache,
            demand_dict,
        )

        return AsyncTaskResponse(
            code=200,
            msg="任务已提交，请轮询查询结果",
            task_id=task_id,
        )

    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"配置错误: {str(exc)}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"服务内部错误: {str(exc)}")


# ============================================================
# GET /api/travel/task/{task_id} —— 查询异步任务
# ============================================================
@app.get(
    "/api/travel/task/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询异步任务状态",
    description=(
        "根据任务 ID 查询异步行程生成任务的状态。\n"
        "- pending: 排队等待中\n"
        "- running: 正在生成\n"
        "- success: 生成成功，data 字段包含行程结果\n"
        "- failed: 生成失败，error 字段包含错误信息"
    ),
    tags=["Travel"],
)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """查询异步任务的状态与结果。

    Args:
        task_id: 任务 ID（由异步提交接口返回，最小 1 字符）。
    """
    # 参数校验
    task_id = (task_id or "").strip()
    if not task_id:
        raise HTTPException(
            status_code=400,
            detail="缺少必填参数: task_id 不能为空",
        )

    try:
        task_service = await get_task_service()
        task = await task_service.get(task_id)

        data: dict[str, Any] | None = None
        if task.status == TaskStatus.SUCCESS and task.result:
            data = task.result

        return TaskStatusResponse(
            code=200,
            task_id=task.task_id,
            status=task.status.value,
            progress=task.progress,
            data=data,
            error=task.error,
        )

    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(exc)}")
