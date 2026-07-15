"""
Agent 全链路可观测追踪器。

功能：
- 记录每个节点的输入输出、执行耗时、异常信息
- 日志按日期存储在 logs/ 目录，结构化 JSONL 格式
- 预留 LangSmith 接入接口（通过 LANGSMITH_API_KEY 环境变量开启）
- 默认使用本地 JSONL 日志，零外部依赖

使用方式：
    from src.utils.tracer import get_tracer

    tracer = get_tracer()
    traced_node = tracer.wrap_node("demand_analyze", demand_analyze_node)
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Optional


# ============================================================
# 配置
# ============================================================
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_DEFAULT_LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")

# 环境变量控制的 LangSmith 开关
_ENABLE_LANGSMITH = bool(os.getenv("LANGSMITH_API_KEY", ""))

# 全局单例
_tracer: Optional["AgentTracer"] = None
_lock = threading.Lock()


def _make_json_serializable(obj: Any, max_depth: int = 3) -> Any:
    """将任意对象转为 JSON 可序列化格式。

    Args:
        obj: 原始对象。
        max_depth: 最大递归深度，防止无限递归。

    Returns:
        JSON-safe 的 Python 原生对象。
    """
    if max_depth <= 0:
        return "<max_depth_exceeded>"

    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item, max_depth - 1) for item in obj[:50]]
    if isinstance(obj, dict):
        return {
            str(k): _make_json_serializable(v, max_depth - 1)
            for k, v in list(obj.items())[:50]
        }
    # 其他类型尝试转字符串
    try:
        return str(obj)[:500]
    except Exception:
        return "<unserializable>"


class AgentTracer:
    """Agent 全链路追踪器。

    以 JSONL 格式记录每次节点执行的全量上下文，
    存储在 logs/trace_YYYY-MM-DD.jsonl 中。
    """

    def __init__(self, log_dir: str = _DEFAULT_LOG_DIR) -> None:
        """初始化追踪器。

        Args:
            log_dir: 日志存储目录，默认 logs/。
        """
        self._log_dir = log_dir
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._trace_count: int = 0
        self._langsmith_enabled = _ENABLE_LANGSMITH
        # 内存级时间线：按调用顺序存储每节点耗时，供性能基线测试采集
        self._timeline: list[dict[str, Any]] = []

        os.makedirs(self._log_dir, exist_ok=True)

        if self._langsmith_enabled:
            self._init_langsmith()

    # ----------------------------------------------------------
    # LangSmith 预留接口
    # ----------------------------------------------------------
    def _init_langsmith(self) -> None:
        """初始化 LangSmith 追踪（需要 langsmith 包）。"""
        try:
            import langsmith  # noqa: F401  # type: ignore
            os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
            os.environ.setdefault(
                "LANGCHAIN_PROJECT",
                os.getenv("LANGSMITH_PROJECT", "travel-planner"),
            )
        except ImportError:
            # LangSmith 未安装，降级为仅本地日志
            pass

    @property
    def langsmith_enabled(self) -> bool:
        """LangSmith 是否已启用。"""
        return self._langsmith_enabled

    # ----------------------------------------------------------
    # 内存级时间线（供性能基线测试程序化采集）
    # ----------------------------------------------------------
    def reset_timeline(self) -> None:
        """清空内存时间线，每次独立测试前调用。"""
        self._timeline = []

    def get_timeline(self) -> list[dict[str, Any]]:
        """返回当前时间线副本，按调用先后排列。

        每项: {"node": str, "duration_ms": float}
        """
        return list(self._timeline)

    def _append_timeline(self, node_name: str, duration_ms: float) -> None:
        """内部方法：追加一条节点耗时到内存时间线。"""
        self._timeline.append({
            "node": node_name,
            "duration_ms": round(duration_ms, 2),
        })

    # ----------------------------------------------------------
    # 节点包装（非侵入式）
    # ----------------------------------------------------------
    def wrap_node(
        self,
        node_name: str,
        node_func: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        """包装节点函数，自动注入追踪逻辑。

        不修改原节点函数代码，仅在调用前后插入日志记录。

        Args:
            node_name: 节点名称（如 "demand_analyze"）。
            node_func: 原始节点函数。

        Returns:
            Callable: 包装后的节点函数，签名与返回值与原函数一致。
        """

        def traced_node(state: dict[str, Any]) -> dict[str, Any]:
            trace_id = self._next_trace_id(node_name)
            start_ts = time.time()

            # 记录输入摘要
            input_summary = {
                k: _make_json_serializable(state.get(k))
                for k in ["current_step", "iteration_count", "error_msg"]
            }
            # 对特定节点记录更多上下文
            if node_name == "demand_analyze":
                input_summary["user_demand"] = _make_json_serializable(
                    state.get("user_demand", {})
                )
            elif node_name == "daily_fill":
                input_summary["iteration_count"] = state.get("iteration_count", 0)

            result: Optional[dict[str, Any]] = None
            error_info: Optional[str] = None

            try:
                result = node_func(state)
                return result
            except Exception as exc:
                error_info = f"{str(exc)}\n{traceback.format_exc()}"
                raise
            finally:
                duration_ms = round((time.time() - start_ts) * 1000, 2)

                output_summary: dict[str, Any] = {}
                if result is not None:
                    output_summary = {
                        "current_step": result.get("current_step", ""),
                        "error_msg": (result.get("error_msg", "") or "")[:200],
                    }
                    if node_name == "daily_fill":
                        output_summary["daily_plans_count"] = len(
                            result.get("daily_plans", [])
                        )
                    elif node_name == "plan_check":
                        cr = result.get("check_result", {})
                        output_summary["is_pass"] = cr.get("is_pass")
                        output_summary["issues_count"] = len(cr.get("issues", []))

                self._write_trace({
                    "trace_id": trace_id,
                    "node": node_name,
                    "session": self._session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": duration_ms,
                    "input": input_summary,
                    "output": output_summary,
                    "error": error_info,
                })
                # 同步写入内存时间线，供性能基线测试程序化采集
                self._append_timeline(node_name, duration_ms)

        return traced_node

    # ----------------------------------------------------------
    # 工具调用追踪
    # ----------------------------------------------------------
    def trace_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        duration_ms: float,
        error: Optional[str] = None,
    ) -> None:
        """记录工具调用（由 agent nodes.py 中的 _safe_invoke_tool 调用）。

        Args:
            tool_name: 工具名称。
            params: 调用参数。
            result: 返回结果。
            duration_ms: 耗时（毫秒）。
            error: 错误信息（成功时为 None）。
        """
        self._write_trace({
            "trace_id": self._next_trace_id(f"tool:{tool_name}"),
            "node": f"tool:{tool_name}",
            "session": self._session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 2),
            "input": _make_json_serializable(params),
            "output": _make_json_serializable(
                result if error is None else None
            ),
            "error": error,
        })

    # ----------------------------------------------------------
    # LLM 调用追踪
    # ----------------------------------------------------------
    def trace_llm_call(
        self,
        prompt_preview: str,
        response_preview: str,
        duration_ms: float,
        token_count: Optional[dict[str, int]] = None,
        error: Optional[str] = None,
    ) -> None:
        """记录 LLM 调用（由 LLMOutputValidator 或节点调用）。

        Args:
            prompt_preview: Prompt 摘要（前 200 字符）。
            response_preview: 响应摘要（前 200 字符）。
            duration_ms: 耗时（毫秒）。
            token_count: 可选 token 消耗统计。
            error: 错误信息。
        """
        self._write_trace({
            "trace_id": self._next_trace_id("llm"),
            "node": "llm_call",
            "session": self._session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 2),
            "input": {"prompt_preview": prompt_preview[:200]},
            "output": {"response_preview": response_preview[:200]},
            "token_count": token_count or {},
            "error": error,
        })

    # ----------------------------------------------------------
    # 通用事件追踪
    # ----------------------------------------------------------
    def write_event(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """写入一条通用追踪事件（供非节点组件使用，如 task_service）。

        Args:
            event_type: 事件类型标识（如 "async_task:task_start"）。
            data: 事件数据载荷。
        """
        self._write_trace({
            "trace_id": self._next_trace_id(event_type),
            "node": event_type,
            "session": self._session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": 0,
            "input": _make_json_serializable(data),
            "output": None,
            "error": None,
        })

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------
    def _next_trace_id(self, prefix: str) -> str:
        """生成自增 trace ID。"""
        self._trace_count += 1
        return f"{prefix}_{self._trace_count:04d}"

    def _get_log_path(self) -> str:
        """获取当日日志文件路径。"""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return os.path.join(self._log_dir, f"trace_{date_str}.jsonl")

    def _write_trace(self, entry: dict[str, Any]) -> None:
        """写入一条追踪记录（JSONL 格式，线程安全）。"""
        log_path = self._get_log_path()

        try:
            line = json.dumps(entry, ensure_ascii=False, default=str)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # 追踪写入失败不应中断业务
            pass


def get_tracer(log_dir: str = _DEFAULT_LOG_DIR) -> AgentTracer:
    """获取全局 AgentTracer 实例（线程安全单例）。

    Args:
        log_dir: 日志目录，仅在首次调用时生效。

    Returns:
        AgentTracer: 全局追踪器实例。
    """
    global _tracer

    if _tracer is not None:
        return _tracer

    with _lock:
        if _tracer is not None:
            return _tracer
        _tracer = AgentTracer(log_dir=log_dir)
        return _tracer
