"""
异步任务管理服务 —— 基于内存队列实现。

功能：
- 提交行程生成任务，返回唯一任务 ID
- 查询任务状态（pending / running / success / failed）及进度
- 超时自动标记失败，防止资源泄漏
- 预留 Redis 扩展接口（替换 _store / _queue 即可）

线程安全：所有公开方法均使用 asyncio.Lock 保护。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# 类型定义
# ---------------------------------------------------------------
class TaskStatus(str, Enum):
    """任务状态枚举。"""
    PENDING = "pending"       # 排队等待中
    RUNNING = "running"       # 执行中
    SUCCESS = "success"       # 执行成功
    FAILED = "failed"         # 执行失败（含超时）


@dataclass
class TaskInfo:
    """任务信息结构体。"""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: str = ""              # 人类可读的进度描述
    result: Optional[dict[str, Any]] = None  # 成功时的行程结果
    error: Optional[str] = None     # 失败时的错误信息
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    timeout: float = 300.0          # 超时时间（秒），默认 5 分钟


# ---------------------------------------------------------------
# 异常
# ---------------------------------------------------------------
class TaskNotFoundError(Exception):
    """指定 task_id 不存在。"""
    pass


class TaskTimeoutError(Exception):
    """任务执行超时。"""
    pass


# ---------------------------------------------------------------
# 任务服务（内存实现）
# ---------------------------------------------------------------
class TaskService:
    """异步任务管理服务。

    基于内存 dict + asyncio.Queue 实现，预留 Redis 扩展接口。
    使用方式：
        svc = TaskService()
        task_id = await svc.submit(lambda: run_travel_planner(demand))
        info = await svc.get(task_id)
    """

    def __init__(self, max_queue_size: int = 64) -> None:
        """初始化任务服务。

        Args:
            max_queue_size: 队列最大容量，超过后提交返回错误。
        """
        self._tasks: dict[str, TaskInfo] = {}
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: Optional[asyncio.Task] = None

    # ---- 公开方法 ----

    async def submit(
        self,
        func: Callable[..., dict[str, Any]],
        *args: Any,
        timeout: float = 300.0,
        **kwargs: Any,
    ) -> str:
        """提交一个异步任务。

        Args:
            func: 要异步执行的函数（如 run_travel_planner）。
            *args: 函数的位置参数。
            timeout: 任务超时时间（秒），默认 300 秒。
            **kwargs: 函数的关键字参数。

        Returns:
            str: 任务 ID（8 字符）。

        Raises:
            RuntimeError: 队列已满。
        """
        task_id = uuid.uuid4().hex[:8]
        task = TaskInfo(task_id=task_id, timeout=timeout)

        async with self._lock:
            self._tasks[task_id] = task

        # 将任务放入队列
        try:
            self._queue.put_nowait((task_id, func, args, kwargs))
        except asyncio.QueueFull:
            async with self._lock:
                task.status = TaskStatus.FAILED
                task.error = "任务队列已满，请稍后重试"
                task.updated_at = time.time()
            raise RuntimeError("任务队列已满")

        # 确保 worker 在运行
        self._ensure_worker()

        # ---- 追踪：任务入队 ----
        self._trace_task_event(task_id, "task_submitted", {
            "status": "pending",
            "timeout": timeout,
        })

        logger.info("任务已提交: task_id=%s, timeout=%ds", task_id, timeout)
        return task_id

    async def get(self, task_id: str) -> TaskInfo:
        """查询任务状态与结果。

        Args:
            task_id: 任务 ID。

        Returns:
            TaskInfo: 任务完整信息。

        Raises:
            TaskNotFoundError: task_id 不存在。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(f"任务不存在: {task_id}")
            return task

    async def complete_immediately(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> bool:
        """将一个任务直接标记为成功（例如缓存命中时跳过执行）。

        Args:
            task_id: 任务 ID。
            result: 结果数据。

        Returns:
            bool: 是否成功写入。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = TaskStatus.SUCCESS
            task.progress = "缓存命中，直接返回"
            task.result = result
            task.updated_at = time.time()
            logger.info("任务直接完成（缓存命中）: task_id=%s", task_id)

        # ---- 追踪：缓存命中 ----
        self._trace_task_event(task_id, "task_cache_hit", {
            "status": "success",
            "source": "cache",
        })
        return True

    async def cancel(self, task_id: str) -> bool:
        """取消一个排队中或执行中的任务。

        注：当前实现仅标记为失败，不中断正在运行的函数。

        Args:
            task_id: 任务 ID。

        Returns:
            bool: 是否成功取消。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                task.status = TaskStatus.FAILED
                task.error = "任务已被取消"
                task.updated_at = time.time()
                logger.info("任务已取消: task_id=%s", task_id)
                return True
            return False

    @property
    def task_count(self) -> int:
        """当前任务总数（不含已清理的任务）。"""
        return len(self._tasks)

    # ---- 内部方法 ----

    def _ensure_worker(self) -> None:
        """确保后台 worker 在运行（幂等）。"""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        """后台 worker：从队列取任务并执行。"""
        while True:
            try:
                task_id, func, args, kwargs = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break  # 队列空，退出（下次提交会重新创建）

            await self._execute_task(task_id, func, *args, **kwargs)

    async def _execute_task(
        self,
        task_id: str,
        func: Callable[..., dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """执行单个任务，包含超时控制与全链路追踪。"""
        start_ts = time.time()

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.RUNNING
            task.progress = "任务开始执行"
            task.updated_at = time.time()

        # ---- 追踪：任务开始 ----
        self._trace_task_event(task_id, "task_start", {
            "status": "running",
            "timeout": task.timeout if task else 300.0,
        })

        error_msg: Optional[str] = None
        result: Optional[dict[str, Any]] = None

        try:
            # 在线程池中执行同步函数，避免阻塞事件循环
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                timeout=task.timeout if task else 300.0,
            )

            duration_ms = round((time.time() - start_ts) * 1000, 2)
            async with self._lock:
                if task_id in self._tasks:
                    t = self._tasks[task_id]
                    # 已被取消则不覆盖
                    if t.status == TaskStatus.FAILED and t.error == "任务已被取消":
                        return
                    # 如果任务已由 complete_immediately 直接完成（如缓存命中），
                    # 则不覆盖其结果（修复 worker 竞态条件）
                    if t.status == TaskStatus.SUCCESS:
                        logger.info(
                            "任务已被 complete_immediately 完成，跳过 worker 写入: task_id=%s",
                            task_id,
                        )
                        return
                    # 检查 Agent 返回的 error_msg，若有错误则标记为失败
                    result_error = ""
                    if isinstance(result, dict):
                        result_error = str(result.get("error_msg", "") or "").strip()
                    if result_error:
                        t.status = TaskStatus.FAILED
                        t.progress = ""
                        t.error = result_error
                        t.result = result
                        t.updated_at = time.time()
                        logger.error(
                            "任务完成但有错误: task_id=%s, duration=%sms, error=%s",
                            task_id, duration_ms, result_error,
                        )
                    else:
                        t.status = TaskStatus.SUCCESS
                        t.progress = "任务完成"
                        t.result = result
                        t.updated_at = time.time()
                        logger.info("任务成功: task_id=%s, duration=%sms", task_id, duration_ms)

            # ---- 追踪：任务成功 ----
            self._trace_task_event(task_id, "task_success", {
                "duration_ms": duration_ms,
                "result_keys": list(result.keys()) if result else [],
            })

        except asyncio.TimeoutError:
            duration_ms = round((time.time() - start_ts) * 1000, 2)
            error_msg = f"任务执行超时（{task.timeout if task else 300.0:.0f} 秒）"
            async with self._lock:
                if task_id in self._tasks:
                    t = self._tasks[task_id]
                    t.status = TaskStatus.FAILED
                    t.progress = ""
                    t.error = error_msg
                    t.updated_at = time.time()
                    logger.error("任务超时: task_id=%s, duration=%sms", task_id, duration_ms)

            # ---- 追踪：任务超时 ----
            self._trace_task_event(task_id, "task_timeout", {
                "duration_ms": duration_ms,
                "error": error_msg,
            })

        except Exception as exc:
            duration_ms = round((time.time() - start_ts) * 1000, 2)
            error_msg = f"任务执行异常: {str(exc)}"
            async with self._lock:
                if task_id in self._tasks:
                    t = self._tasks[task_id]
                    # 已被取消则不覆盖
                    if t.status == TaskStatus.FAILED and t.error == "任务已被取消":
                        return
                    t.status = TaskStatus.FAILED
                    t.progress = ""
                    t.error = error_msg
                    t.updated_at = time.time()
                    logger.exception("任务失败: task_id=%s", task_id)

            # ---- 追踪：任务失败 ----
            self._trace_task_event(task_id, "task_failed", {
                "duration_ms": duration_ms,
                "error": error_msg,
            })

    @staticmethod
    def _trace_task_event(
        task_id: str,
        event: str,
        detail: dict[str, Any],
    ) -> None:
        """将任务生命周期事件写入全链路追踪系统。"""
        try:
            from src.utils.tracer import get_tracer
            tracer = get_tracer()
            # 写入追踪日志文件
            tracer.write_event(
                event_type=f"async_task:{event}",
                data={
                    "task_id": task_id,
                    "event": event,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    **detail,
                },
            )
        except Exception:
            # 追踪失败不应影响业务
            pass


# ---------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------
_task_service: Optional[TaskService] = None
_task_service_lock = asyncio.Lock()


async def get_task_service() -> TaskService:
    """获取全局 TaskService 实例（异步线程安全单例）。"""
    global _task_service
    if _task_service is not None:
        return _task_service

    async with _task_service_lock:
        if _task_service is not None:
            return _task_service
        _task_service = TaskService()
        return _task_service
