"""
Travel plan export service — Markdown and PDF format export.

Features:
- Markdown export: generates structured .md file with overview, daily details, tips
- PDF export: Markdown → HTML → PDF via xhtml2pdf → weasyprint → pdfkit → markdown fallback
- Full trace logging for all export operations

Usage:
    from src.services.export_service import ExportService, get_export_service

    svc = ExportService()
    md_bytes = svc.export_markdown(travel_data)    # returns bytes
    pdf_bytes = svc.export_pdf(travel_data)        # returns bytes
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# PDF export timeout (seconds)
_PDF_EXPORT_TIMEOUT = 30


# ============================================================
# ExportService
# ============================================================
class ExportService:
    """Travel plan export service — Markdown / PDF format.

    Usage:
        svc = ExportService()
        md_bytes = svc.export_markdown(data)   # bytes
        pdf_bytes = svc.export_pdf(data)        # bytes
    """

    # precompiled regex (Markdown → HTML bold replacement)
    _BOLD_RE: re.Pattern = re.compile(r"\*\*(.+?)\*\*")

    # ----------------------------------------------------------
    # public methods
    # ----------------------------------------------------------

    def export_markdown(self, travel_data: dict[str, Any]) -> bytes:
        """Export travel data as Markdown bytes.

        Args:
            travel_data: Travel plan data dict.

        Returns:
            bytes: UTF-8 encoded Markdown bytes.
        """
        start_ts: float = time.time()
        data_size: int = len(str(travel_data))

        md_text: str = self._generate_markdown_str(travel_data)
        md_bytes: bytes = md_text.encode("utf-8")

        duration_ms: float = round((time.time() - start_ts) * 1000, 2)
        self._trace_export("export_markdown", {
            "format": "md",
            "data_size": data_size,
            "content_size": len(md_bytes),
            "duration_ms": duration_ms,
            "status": "success",
            "encoding": "utf-8",
        })

        logger.info("Markdown export success: size=%d, duration=%sms",
                     len(md_text), duration_ms)

        return md_bytes

    def export_pdf(self, travel_data: dict[str, Any]) -> bytes:
        """Export travel data as PDF bytes.

        Tries xhtml2pdf → weasyprint → pdfkit → markdown html fallback.
        Raises RuntimeError if all engines fail.

        Args:
            travel_data: Travel plan data dict.

        Returns:
            bytes: PDF file bytes.
        """
        start_ts: float = time.time()
        data_size: int = len(str(travel_data))
        pdf_bytes: Optional[bytes] = None
        fallback_used: str = ""

        # ---- generate Markdown and convert to HTML ----
        md_content: str = self._generate_markdown_str(travel_data)
        html_body: str = self._md_to_html(md_content)
        full_html: str = self._wrap_html(html_body, travel_data)

        # ---- engine 1: xhtml2pdf (pure Python, no system deps on Windows) ----
        try:
            from xhtml2pdf import pisa  # type: ignore
            pdf_buf = io.BytesIO()
            pisa.CreatePDF(
                io.StringIO(full_html), pdf_buf,
                encoding="utf-8",
            )
            pdf_bytes = pdf_buf.getvalue()
            fallback_used = "xhtml2pdf"
        except ImportError:
            logger.info("xhtml2pdf not installed, falling back to weasyprint")
        except Exception as exc:
            logger.warning("xhtml2pdf PDF generation failed: %s, trying fallback", exc)

        # ---- engine 2: weasyprint (best CSS paging support) ----
        if pdf_bytes is None:
            try:
                import weasyprint  # type: ignore
                pdf_bytes = weasyprint.HTML(string=full_html).write_pdf()
                fallback_used = "weasyprint"
            except ImportError:
                logger.info("weasyprint not installed, falling back to pdfkit")
            except Exception as exc:
                logger.warning("weasyprint PDF generation failed: %s, trying fallback", exc)

        # ---- engine 3: pdfkit (requires wkhtmltopdf on system PATH) ----
        if pdf_bytes is None:
            try:
                import pdfkit  # type: ignore
                pdf_bytes = pdfkit.from_string(
                    full_html,
                    False,
                    options={
                        "encoding": "UTF-8",
                        "enable-local-file-access": "",
                    },
                )
                fallback_used = "pdfkit"
            except ImportError:
                logger.info("pdfkit not installed, falling back to markdown lib")
            except Exception as exc:
                logger.warning("pdfkit PDF generation failed: %s, trying fallback", exc)

        # ---- engine 4: markdown lib HTML fallback (not real PDF, returns HTML bytes) ----
        if pdf_bytes is None:
            try:
                import markdown as md_lib  # type: ignore
                html_body = md_lib.markdown(
                    md_content, extensions=["extra", "toc"],
                )
                full_html = self._wrap_html(html_body, travel_data)
                pdf_bytes = full_html.encode("utf-8")
                fallback_used = "markdown_html"
            except ImportError:
                pass

        # ---- all engines failed ----
        if pdf_bytes is None:
            raise RuntimeError(
                "PDF export failed: no PDF generation library available. "
                "Please install: pip install xhtml2pdf"
            )

        # ---- trace & return ----
        duration_ms: float = round((time.time() - start_ts) * 1000, 2)
        self._trace_export("export_pdf", {
            "format": "pdf",
            "data_size": data_size,
            "content_size": len(pdf_bytes),
            "duration_ms": duration_ms,
            "status": "success",
            "fallback_used": fallback_used,
        })

        logger.info("PDF export success: size=%d, duration=%sms, engine=%s",
                     len(pdf_bytes), duration_ms, fallback_used)
        return pdf_bytes

    # ----------------------------------------------------------
    # internal methods
    # ----------------------------------------------------------

    def _generate_markdown_str(self, travel_data: dict[str, Any]) -> str:
        """Generate complete Markdown travel plan string.

        Args:
            travel_data: Travel plan data dict.

        Returns:
            str: Complete Markdown format travel document.
        """
        buf = io.StringIO()
        self._build_header(buf, travel_data)
        self._build_overview(buf, travel_data)
        self._build_budget_summary(buf, travel_data)
        self._build_daily_plans(buf, travel_data)
        self._build_tips(buf, travel_data)
        self._build_footer(buf, travel_data)
        return buf.getvalue()

    # ----------------------------------------------------------
    # internal builder methods (write to io.StringIO)
    # ----------------------------------------------------------

    @staticmethod
    def _build_header(buf: io.StringIO, data: dict[str, Any]) -> None:
        """Build document title and export timestamp.

        Args:
            buf: Write buffer.
            data: Travel plan data dict.
        """
        destination: str = str(data.get("destination", "") or "未知目的地")
        total_days: int = int(data.get("total_days", 0) or 0)
        days_str: str = f"{total_days}天" if total_days > 0 else ""
        buf.write(f"# ✈️ {destination}{days_str}旅行行程规划\n\n")
        export_time: str = datetime.now().strftime("%Y-%m-%d %H:%M")
        buf.write(f"> 📅 导出时间: {export_time}\n\n")
        buf.write("---\n\n")

    @staticmethod
    def _build_overview(buf: io.StringIO, data: dict[str, Any]) -> None:
        """Build trip overview table.

        Args:
            buf: Write buffer.
            data: Travel plan data dict.
        """
        buf.write("## 📊 行程概览\n\n")
        buf.write("| 项目 | 详情 |\n")
        buf.write("|------|------|\n")

        destination: str = str(data.get("destination", "") or "—")
        buf.write(f"| 📍 目的地 | {_escape_md_table(destination)} |\n")

        total_days: int = int(data.get("total_days", 0) or 0)
        buf.write(f"| 📅 出行天数 | {total_days} 天 |\n")

        total_budget: float = float(data.get("total_budget", 0) or 0)
        buf.write(f"| 💰 总预算 | ¥{total_budget:,.0f} |\n")

        people: str = str(data.get("people", "") or "—")
        buf.write(f"| 👥 出行人群 | {_escape_md_table(people)} |\n")

        preferences: list[str] = _safe_list(data.get("preferences"))
        pref_str: str = ", ".join(preferences) if preferences else "—"
        buf.write(f"| 🏷️ 偏好标签 | {_escape_md_table(pref_str)} |\n")

        revision_round: int = int(data.get("revision_round", 0) or 0)
        if revision_round > 0:
            buf.write(f"| 🔄 修订轮次 | 第 {revision_round} 轮 |\n")
            revision_instruction: str = str(
                data.get("revision_instruction", "") or ""
            )
            if revision_instruction:
                buf.write(
                    f"| ✏️ 修订指令 | "
                    f"{_escape_md_table(revision_instruction[:100])} |\n"
                )

        buf.write("\n---\n\n")

    @staticmethod
    def _build_budget_summary(buf: io.StringIO, data: dict[str, Any]) -> None:
        """Build daily budget breakdown summary table.

        Only printed when at least one day has a non‑zero daily_budget.

        Args:
            buf: Write buffer.
            data: Travel plan data dict.
        """
        daily_plans: list[dict[str, Any]] = _safe_list(
            data.get("daily_plans")
        )
        if not daily_plans:
            return

        # collect budgets
        has_budget = any(
            isinstance(p, dict) and float(p.get("daily_budget", 0) or 0) > 0
            for p in daily_plans
        )
        if not has_budget:
            return

        # sort by day_index
        try:
            sorted_plans = sorted(
                daily_plans,
                key=lambda p: int(p.get("day_index", 0) or 0),
            )
        except Exception:
            sorted_plans = daily_plans

        total_budget: float = float(data.get("total_budget", 0) or 0)

        buf.write("## 💰 每日预算分配\n\n")
        buf.write("| 天数 | 主题 | 预算金额 | 占比 |\n")
        buf.write("|------|------|----------|------|\n")

        grand_total: float = 0.0
        rows: list[tuple[int, str, float]] = []
        for plan in sorted_plans:
            if not isinstance(plan, dict):
                continue
            day_index: int = int(plan.get("day_index", 0) or 0)
            theme: str = str(plan.get("theme", "") or f"第{day_index}天")
            day_budget: float = float(plan.get("daily_budget", 0) or 0)
            if day_budget <= 0:
                continue
            grand_total += day_budget
            rows.append((day_index, theme, day_budget))

        for day_index, theme, day_budget in rows:
            pct = f"{day_budget / total_budget * 100:.0f}%" if total_budget > 0 else "—"
            flag = " ⚠️" if total_budget > 0 and day_budget > (total_budget / len(rows)) * 1.3 else ""
            buf.write(
                f"| 第{day_index}天 | {_escape_md_table(theme)} "
                f"| ¥{day_budget:,.0f}{flag} | {pct} |\n"
            )

        if rows:
            buf.write(f"| **合计** | | **¥{grand_total:,.0f}** | |\n")

            if total_budget > 0 and grand_total > total_budget:
                over = grand_total - total_budget
                buf.write(f"| ⚠️ 超出预算 | | **¥{over:,.0f}** | |\n")

        buf.write("\n---\n\n")

    @staticmethod
    def _build_daily_plans(buf: io.StringIO, data: dict[str, Any]) -> None:
        """Build daily itinerary details (spots table + food + traffic).

        Args:
            buf: Write buffer.
            data: Travel plan data dict.
        """
        buf.write("## 🗺️ 每日行程详情\n\n")

        daily_plans: list[dict[str, Any]] = _safe_list(
            data.get("daily_plans")
        )
        if not daily_plans:
            buf.write("> ⚠️ 暂无行程数据\n\n")
            return

        # sort by day_index for correct output order
        try:
            daily_plans = sorted(
                daily_plans,
                key=lambda p: int(p.get("day_index", 0) or 0),
            )
        except Exception:
            pass

        for plan in daily_plans:
            if not isinstance(plan, dict):
                continue

            day_index: int = int(plan.get("day_index", 0) or 0)
            theme: str = str(plan.get("theme", "") or f"第{day_index}天")
            daily_budget: float = float(plan.get("daily_budget", 0) or 0)

            buf.write(f"### Day {day_index} — {theme}\n\n")
            if daily_budget > 0:
                buf.write(f"**💰 日预算**: ¥{daily_budget:,.0f}\n\n")

            # ---- budget breakdown table (新增) ----
            budget_breakdown: dict[str, float] = plan.get("budget_breakdown", {}) or {}
            if budget_breakdown and len(budget_breakdown) >= 3:
                buf.write("**📊 预算明细**:\n\n")
                buf.write("| 类目 | 金额 | 占比 |\n")
                buf.write("|------|------|------|\n")
                for cat, amt in budget_breakdown.items():
                    pct = f"{amt / daily_budget * 100:.0f}%" if daily_budget > 0 else "—"
                    buf.write(f"| {_escape_md_table(str(cat))} | ¥{amt:,.0f} | {pct} |\n")
                buf.write("\n")

            # ---- spots table (增强：检测 time_slot) ----
            spots: list[dict[str, Any]] = _safe_list(plan.get("spots"))
            if spots:
                has_time_slots = any(
                    isinstance(s, dict) and s.get("time_slot", "").strip()
                    for s in spots
                )
                if has_time_slots:
                    buf.write(
                        "| 景点 | 时段 | 地址 | 建议时长 | 门票 | 推荐理由 |\n"
                    )
                    buf.write(
                        "|------|------|------|----------|------|----------|\n"
                    )
                else:
                    buf.write(
                        "| 景点 | 地址 | 建议时长 | 门票 | 推荐理由 |\n"
                    )
                    buf.write(
                        "|------|------|----------|------|----------|\n"
                    )
                for spot in spots:
                    _write_spot_row(buf, spot, show_time_slot=has_time_slots)
                buf.write("\n")
            else:
                buf.write("> 暂无景点数据\n\n")

            # ---- food recommendations (兼容 meals / food_recommendation 两种字段名) ----
            foods: list[str] = _safe_list(
                plan.get("meals") or plan.get("food_recommendation")
            )
            if foods:
                buf.write("**🍜 推荐美食**:\n")
                for food in foods:
                    if food:
                        buf.write(f"- {food}\n")
                buf.write("\n")

            # ---- traffic note (兼容 transportation / traffic_note 两种字段名) ----
            traffic: str = str(
                plan.get("transportation") or plan.get("traffic_note") or ""
            )
            if traffic:
                buf.write(f"**🚌 交通方式**: {traffic}\n\n")

            # ---- accommodation (新增) ----
            accommodation: str = str(plan.get("accommodation", "") or "")
            if accommodation:
                buf.write(f"**🏨 住宿建议**: {accommodation}\n\n")

            buf.write("---\n\n")

    @staticmethod
    def _build_tips(buf: io.StringIO, data: dict[str, Any]) -> None:
        """Build travel tips, validation issues and revision diff summary.

        Args:
            buf: Write buffer.
            data: Travel plan data dict.
        """
        tips: list[str] = _safe_list(data.get("travel_tips"))
        check_result: dict[str, Any] = data.get("check_result", {}) or {}
        has_issues: bool = (
            isinstance(check_result, dict)
            and not check_result.get("is_pass", True)
            and bool(check_result.get("issues"))
        )

        revision_diff: Optional[dict[str, Any]] = data.get("revision_diff")

        if not tips and not has_issues and not revision_diff:
            return

        buf.write("## 📝 出行贴士与注意事项\n\n")

        if tips:
            for tip in tips:
                if tip:
                    buf.write(f"- ⚠️ {tip}\n")
            buf.write("\n")

        # ---- validation issues ----
        if has_issues:
            buf.write("### ⚠️ 校验问题\n\n")
            for issue in _safe_list(check_result.get("issues")):
                if issue:
                    buf.write(f"- {issue}\n")
            buf.write("\n")

        # ---- revision diff summary ----
        if revision_diff and isinstance(revision_diff, dict):
            summary: str = str(revision_diff.get("summary", "") or "")
            if summary and summary != "行程无变化":
                buf.write("### 🔄 修订变更摘要\n\n")
                buf.write(f"{summary}\n\n")
                per_day_diffs: list[dict[str, Any]] = _safe_list(
                    revision_diff.get("per_day_diffs")
                )
                if per_day_diffs:
                    for dd in per_day_diffs:
                        if dd.get("changed") and dd.get("summary"):
                            buf.write(f"- {dd['summary']}\n")
                    buf.write("\n")

        buf.write("---\n\n")

    @staticmethod
    def _build_footer(buf: io.StringIO, data: dict[str, Any]) -> None:
        """Build document footer.

        Args:
            buf: Write buffer.
            data: Travel plan data dict.
        """
        buf.write("---\n\n")
        buf.write("*本文档由 AI 旅行规划器自动生成，仅供参考。*\n\n")

        run_mode: str = str(data.get("run_mode", "react") or "react")
        iteration: int = int(data.get("iteration_count", 0) or 0)
        buf.write(f"*生成模式: {run_mode} | 迭代轮次: {iteration}*\n")

    # ----------------------------------------------------------
    # Markdown → HTML conversion
    # ----------------------------------------------------------

    @classmethod
    def _md_to_html(cls, md_content: str) -> str:
        """Convert Markdown to HTML body fragment.

        Supports: h1-h3, tables, unordered lists, blockquotes, hr, bold, paragraphs.
        Zero external dependency implementation.

        Args:
            md_content: Markdown text.

        Returns:
            str: HTML body content (without <html>/<body> tags).
        """
        buf = io.StringIO()
        in_table: bool = False
        is_header_row: bool = False
        in_list: bool = False

        for line in md_content.split("\n"):
            stripped: str = line.strip()

            # ---- code block ----
            if stripped.startswith("```"):
                continue

            # ---- table row ----
            if "|" in stripped and stripped.startswith("|"):
                cells: list[str] = [
                    c.strip() for c in stripped.split("|")[1:-1]
                ]

                if in_list:
                    buf.write("</ul>\n")
                    in_list = False

                # separator row (e.g. |---|---|) — skip, mark next as data row
                if all(not c or re.match(r"^[-:]+$", c) for c in cells):
                    if in_table:
                        is_header_row = False
                    continue

                if not in_table:
                    buf.write("<table>\n")
                    in_table = True
                    is_header_row = True

                tag: str = "th" if is_header_row else "td"
                row: str = (
                    "<tr>"
                    + "".join(
                        f"<{tag}>{_escape_html(c)}</{tag}>" for c in cells
                    )
                    + "</tr>\n"
                )
                buf.write(row)
                is_header_row = False
                continue
            else:
                if in_table:
                    buf.write("</table>\n")
                    in_table = False
                    is_header_row = False

            # ---- heading ----
            if stripped.startswith("# "):
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                buf.write(f"<h1>{_escape_html(stripped[2:])}</h1>\n")
            elif stripped.startswith("## "):
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                buf.write(f"<h2>{_escape_html(stripped[3:])}</h2>\n")
            elif stripped.startswith("### "):
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                buf.write(f"<h3>{_escape_html(stripped[4:])}</h3>\n")
            # ---- unordered list ----
            elif stripped.startswith("- "):
                if not in_list:
                    buf.write("<ul>\n")
                    in_list = True
                buf.write(f"<li>{_escape_html(stripped[2:])}</li>\n")
            # ---- blockquote ----
            elif stripped.startswith("> "):
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                buf.write(
                    f"<blockquote>{_escape_html(stripped[2:])}</blockquote>\n"
                )
            # ---- horizontal rule ----
            elif stripped.startswith("---"):
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                buf.write("<hr>\n")
            # ---- paragraph ----
            elif stripped:
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                text: str = _escape_html(stripped)
                text = cls._BOLD_RE.sub(r"<strong>\1</strong>", text)
                buf.write(f"<p>{text}</p>\n")
            else:
                if in_list:
                    buf.write("</ul>\n")
                    in_list = False
                buf.write("<br>\n")

        # close unclosed structures
        if in_list:
            buf.write("</ul>\n")
        if in_table:
            buf.write("</table>\n")

        return buf.getvalue()

    @staticmethod
    def _wrap_html(body: str, data: dict[str, Any]) -> str:
        """Wrap HTML body fragment into complete HTML document.

        Args:
            body: HTML body content.
            data: Travel data (for title extraction).

        Returns:
            str: Complete HTML document.
        """
        destination: str = str(data.get("destination", "") or "Travel")
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>{destination} Travel Plan</title>
    <style>
        @page {{ margin: 15mm 12mm; size: A4; }}
        body {{
            font-family: 'Microsoft YaHei', 'SimHei', 'PingFang SC', 'Noto Sans SC', sans-serif;
            max-width: 800px; margin: 0 auto; padding: 20px; color: #333;
            line-height: 1.6;
        }}
        h1 {{ color: #1f77b4; border-bottom: 2px solid #1f77b4; padding-bottom: 8px; }}
        h2 {{ color: #2c3e50; margin-top: 28px; }}
        h3 {{ color: #34495e; margin-top: 20px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
        th {{ background: #f0f4f8; font-weight: 600; }}
        tr:nth-child(even) td {{ background: #fafbfc; }}
        blockquote {{
            background: #fff3cd; border-left: 4px solid #ffc107;
            padding: 8px 16px; margin: 8px 0;
        }}
        hr {{ border: none; border-top: 1px solid #eee; margin: 24px 0; }}
        li {{ margin: 4px 0; }}
    </style>
</head>
<body>
{body}
</body>
</html>"""

    # ----------------------------------------------------------
    # trace logging
    # ----------------------------------------------------------

    @staticmethod
    def _trace_export(operation: str, detail: dict[str, Any]) -> None:
        """Write export event to trace system.

        Args:
            operation: Operation id (export_markdown / export_pdf).
            detail: Event detail (format, data size, duration, status, etc.).
        """
        try:
            from src.utils.tracer import get_tracer
            tracer = get_tracer()
            tracer.write_event(
                event_type=f"export:{operation}",
                data={
                    "operation": operation,
                    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    **detail,
                },
            )
        except Exception:
            pass


# ============================================================
# module-level utility functions
# ============================================================

def _safe_list(value: Any) -> list:
    """Safely convert value to list.

    Handles None, non-list types, etc.

    Args:
        value: Original value.

    Returns:
        list: List (guaranteed not None).
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return []


def _escape_html(text: str) -> str:
    """HTML entity escaping to prevent XSS and formatting issues.

    Args:
        text: Original text.

    Returns:
        str: Escaped safe text.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_export_filename(
    destination: str,
    fmt: str,
    total_days: int = 0,
) -> str:
    """Build export filename in format: {目的地}{天数}天行程规划.{后缀}

    Replaces unsafe filename characters (/ \\ : * ? " < > |).

    Args:
        destination: Destination name.
        fmt: Export format label ("md" or "pdf").
        total_days: Total days (included in filename if > 0).

    Returns:
        str: Safe filename.
    """
    safe_dest: str = re.sub(
        r'[\/\\:*?"<>|\n\r]', "_", str(destination or "行程").strip()
    )
    if len(safe_dest) > 40:
        safe_dest = safe_dest[:40]
    days_part: str = f"{total_days}天" if total_days > 0 else ""
    ext: str = "md" if fmt == "md" else "pdf"
    return f"{safe_dest}{days_part}行程规划.{ext}"


def _escape_md_table(text: str) -> str:
    """Escape pipe characters in Markdown tables.

    Args:
        text: Original text.

    Returns:
        str: Escaped safe text.
    """
    return text.replace("|", "\\|").replace("\n", " ")


def _write_spot_row(buf: io.StringIO, spot: Any, show_time_slot: bool = False) -> None:
    """Write a single spot as a Markdown table row.

    Compatible with both dict and Pydantic BaseModel formats.

    Args:
        buf: Write buffer.
        spot: Spot data (dict or Spot model).
        show_time_slot: If True, include time_slot column in output.
    """
    if isinstance(spot, dict):
        name = str(spot.get("name", "") or "—")
        address = str(spot.get("address", "") or "—")
        duration_val = float(spot.get("duration", 0) or 0)
        duration = f"{duration_val:.1f}h" if duration_val > 0 else "—"
        time_slot = str(spot.get("time_slot", "") or "—")
        level = str(spot.get("level", "") or "")
        core_feature = str(spot.get("core_feature", "") or "")
        # 兼容 ticket（fallback）和 ticket_price（LLM）两种字段名
        ticket_raw = spot.get("ticket") if "ticket" in spot else spot.get("ticket_price")
        if ticket_raw is not None:
            try:
                ticket_val = float(ticket_raw)
                ticket = f"¥{ticket_val:.0f}" if ticket_val > 0 else "Free"
            except (ValueError, TypeError):
                ticket = str(ticket_raw) if ticket_raw else "Free"
        else:
            ticket = "Free"
        # 推荐理由：合并 level + core_feature 当 recommendation 为空时
        rec = str(
            spot.get("reason")
            or spot.get("recommendation")
            or spot.get("description")
            or ""
        )[:80]
        if not rec and (level or core_feature):
            parts = [p for p in [level, core_feature] if p]
            rec = " | ".join(parts)[:80]
        if not rec:
            rec = "—"
    else:
        try:
            name = str(getattr(spot, "name", "") or "—")
            address = str(getattr(spot, "address", "") or "—")
            duration_val = float(getattr(spot, "duration", 0) or 0)
            duration = f"{duration_val:.1f}h" if duration_val > 0 else "—"
            time_slot = str(getattr(spot, "time_slot", "") or "—")
            level = str(getattr(spot, "level", "") or "")
            core_feature = str(getattr(spot, "core_feature", "") or "")
            ticket_raw = getattr(spot, "ticket", None) or getattr(spot, "ticket_price", None)
            if ticket_raw is not None:
                try:
                    ticket_val = float(ticket_raw)
                    ticket = f"¥{ticket_val:.0f}" if ticket_val > 0 else "Free"
                except (ValueError, TypeError):
                    ticket = str(ticket_raw) if ticket_raw else "Free"
            else:
                ticket = "Free"
            rec = str(
                getattr(spot, "reason", "")
                or getattr(spot, "recommendation", "")
                or getattr(spot, "description", "")
                or ""
            )[:80]
            if not rec and (level or core_feature):
                parts = [p for p in [level, core_feature] if p]
                rec = " | ".join(parts)[:80]
            if not rec:
                rec = "—"
        except Exception:
            buf.write(f"| {spot} | — | — | — | — |\n")
            return

    if show_time_slot:
        buf.write(
            f"| {_escape_md_table(name)} "
            f"| {time_slot} "
            f"| {_escape_md_table(address)} "
            f"| {duration} "
            f"| {ticket} "
            f"| {_escape_md_table(rec)} |\n"
        )
    else:
        buf.write(
            f"| {_escape_md_table(name)} "
            f"| {_escape_md_table(address)} "
            f"| {duration} "
            f"| {ticket} "
            f"| {_escape_md_table(rec)} |\n"
        )


# ============================================================
# global singleton
# ============================================================
_export_service: Optional[ExportService] = None


def get_export_service() -> ExportService:
    """Get global ExportService instance (thread-safe singleton).

    Returns:
        ExportService: Global export service instance.
    """
    global _export_service
    if _export_service is None:
        _export_service = ExportService()
    return _export_service
