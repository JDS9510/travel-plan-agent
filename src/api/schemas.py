"""
API 接口数据模型 —— 基于 Pydantic v2，所有字段与业务层 UserDemand 对齐。

字段约束完全兼容 src/schemas/models.py 中的校验规则，
避免接口层与业务层之间出现数据不一致。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================
# TravelPlanRequest —— 行程规划请求体
# ============================================================
class TravelPlanRequest(BaseModel):
    """行程规划请求体，字段校验规则与业务层 UserDemand 保持一致。

    Examples:
        >>> req = TravelPlanRequest(
        ...     destination="成都",
        ...     days=3,
        ...     total_budget=3000.0,
        ...     people="一家三口",
        ...     preferences=["美食", "亲子"],
        ... )
    """

    destination: str = Field(
        ...,
        min_length=1,
        description="目的地城市，必填",
        examples=["成都"],
    )
    days: int = Field(
        ...,
        ge=1,
        description="出行天数，必填，>=1",
        examples=[3],
    )
    total_budget: float = Field(
        ...,
        ge=0,
        description="总预算上限（元），必填，>=0",
        examples=[3000.0],
    )
    people: str = Field(
        ...,
        min_length=1,
        description="出行人群描述，必填",
        examples=["一家三口"],
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="偏好标签，默认空列表",
        examples=[["美食", "亲子"]],
    )
    remark: str = Field(
        default="",
        description="补充要求，默认空字符串",
        examples=["不要早起，行程宽松一点"],
    )


# ============================================================
# ApiResponse —— 统一响应格式
# ============================================================
class ApiResponse(BaseModel):
    """统一 API 响应格式，所有接口均使用此结构返回。

    Examples:
        >>> ApiResponse(code=200, msg="success", data={"daily_plans": [...]})
        >>> ApiResponse(code=500, msg="服务内部错误")
    """

    code: int = Field(
        default=200,
        description="业务状态码，200 成功 / 500 失败",
    )
    msg: str = Field(
        default="success",
        description="响应消息",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="响应数据载荷，成功时包含完整行程",
    )


# ============================================================
# AsyncTaskResponse —— 异步任务响应
# ============================================================
class AsyncTaskResponse(BaseModel):
    """异步任务提交响应。

    Examples:
        >>> AsyncTaskResponse(code=200, msg="任务已提交", task_id="a1b2c3d4")
    """

    code: int = Field(
        default=200,
        description="业务状态码",
    )
    msg: str = Field(
        default="任务已提交，请轮询查询结果",
        description="响应消息",
    )
    task_id: str = Field(
        default="",
        description="任务 ID，用于轮询查询状态",
    )


# ============================================================
# TaskStatusResponse —— 任务状态查询响应
# ============================================================
class TaskStatusResponse(BaseModel):
    """异步任务状态查询响应。

    Examples:
        >>> TaskStatusResponse(
        ...     code=200,
        ...     task_id="a1b2c3d4",
        ...     status="success",
        ...     progress="任务完成",
        ...     data={"daily_plans": [...]},
        ... )
    """

    code: int = Field(
        default=200,
        description="业务状态码",
    )
    task_id: str = Field(
        ...,
        description="任务 ID",
    )
    status: str = Field(
        ...,
        description="任务状态: pending / running / success / failed",
    )
    progress: str = Field(
        default="",
        description="进度描述",
    )
    data: Optional[dict[str, Any]] = Field(
        default=None,
        description="成功时的结果数据",
    )
    error: Optional[str] = Field(
        default=None,
        description="失败时的错误信息",
    )


# ============================================================
# ReviseRequest —— 行程修改请求
# ============================================================
class ReviseRequest(BaseModel):
    """行程修改请求体 —— 基于已有行程执行增量修改。

    支持两种模式：
    1. task_id 模式（推荐）：传入异步任务的 task_id，服务端自动查询原始行程。
    2. original_data 模式（兼容）：直接传入完整行程数据。

    Examples:
        # task_id 模式（推荐）
        >>> ReviseRequest(
        ...     task_id="a1b2c3d4",
        ...     modify_prompt="把第二天的景点换成亲子类",
        ... )

        # original_data 模式（兼容旧版）
        >>> ReviseRequest(
        ...     original_data={...},
        ...     instruction="把第二天的景点换成亲子类",
        ... )
    """

    task_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="原始行程任务 ID（异步生成返回的 task_id），与 original_data 二选一",
        examples=["a1b2c3d4"],
    )
    modify_prompt: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="修改指令（自然语言），如「把第二天的景点换成亲子类」，与 instruction 等效",
        examples=["把第二天的景点换成亲子类", "总预算降到2000元以内"],
    )
    original_data: Optional[dict[str, Any]] = Field(
        default=None,
        description="原始行程数据（generate 或上一轮 revise 返回的 data 字段），与 task_id 二选一",
    )
    instruction: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="修改指令（兼容旧字段），与 modify_prompt 等效",
        examples=["把第二天的景点换成亲子类", "总预算降到2000元以内"],
    )


# ============================================================
# ExportRequest —— 直接导出行程数据（无需 task_id）
# ============================================================
class ExportRequest(BaseModel):
    """直接导出行程数据请求 —— 用于同步生成场景（无 task_id）。

    Examples:
        >>> ExportRequest(travel_data={...}, format="md")
    """

    travel_data: dict[str, Any] = Field(
        ...,
        description="行程数据（generate 或 revise 返回的 data 字段）",
    )
    format: str = Field(
        default="md",
        pattern="^(md|pdf)$",
        description="导出格式: md 或 pdf",
    )
