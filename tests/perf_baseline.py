"""
行程生成全链路耗时基线测试 —— 3 类城市 × 3 轮重复，采集稳定性能基线。

不改动任何业务逻辑，仅通过 AgentTracer 的 in-memory timeline 采集每节点耗时。
运行方式: python tests/perf_baseline.py
"""
import io
import json
import logging
import os
import statistics
import sys
import time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from src.agent import run_travel_planner
from src.utils.tracer import get_tracer

# ============================================================
# 配置
# ============================================================
RUNS_PER_CASE = 3

TEST_CASES: list[dict] = [
    {
        "name":    "热门城市-成都",
        "destination": "成都", "days": 3, "total_budget": 2000,
        "people": "2人", "preferences": ["美食", "休闲"],
    },
    {
        "name":    "地级市-郑州",
        "destination": "郑州", "days": 3, "total_budget": 2000,
        "people": "2人", "preferences": ["城市观光"],
    },
    {
        "name":    "小众城市-平顶山",
        "destination": "平顶山", "days": 3, "total_budget": 2000,
        "people": "2人", "preferences": ["山水", "休闲"],
    },
]

# 核心节点：tracer 中的 node_name → 报告中的显示名
CORE_NODES: dict[str, str] = {
    "demand_analyze":   "需求解析",
    "outline_generate": "景点检索+框架生成",
    "daily_fill":       "每日行程填充",
    "fact_check":       "事实校验",
    "plan_check":       "机械校验",
    "result_summary":   "结果汇总",
}

# 按报告顺序列出
REPORT_ORDER = ["demand_analyze", "outline_generate", "daily_fill",
                "fact_check", "plan_check", "result_summary"]

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "tests", "output")
REPORT_PATH = os.path.join(OUTPUT_DIR, "PERF_BASELINE_REPORT.md")

# ============================================================
# 数据结构
# ============================================================
# 所有原始记录: list of dict
ALL_ROWS: list[dict] = []


def run_one(case: dict, run_idx: int) -> dict:
    """执行单次全链路并采集每节点耗时。"""
    tracer = get_tracer()
    tracer.reset_timeline()

    dest = case["destination"]
    label = f"{case['name']} R{run_idx + 1}"

    print(f"\n{'─'*60}")
    print(f"  [{label}] 开始... dest={dest} prefs={case['preferences']}")
    print(f"{'─'*60}")

    t0 = time.time()
    result = run_travel_planner({
        "destination": case["destination"],
        "days": case["days"],
        "total_budget": case["total_budget"],
        "people": case["people"],
        "preferences": case["preferences"],
    })
    total_sec = round(time.time() - t0, 2)

    # 采集各节点耗时
    timeline = tracer.get_timeline()
    node_ms: dict[str, float] = {}
    for entry in timeline:
        n = entry["node"]
        # 多次调用累加（如 daily_fill 可能因重试被多次调用）
        node_ms[n] = node_ms.get(n, 0) + entry["duration_ms"]

    # 提取工具调用耗时（tool: 前缀的条目）
    tool_ms: dict[str, float] = {}
    for entry in timeline:
        n = entry["node"]
        if n.startswith("tool:"):
            tool_name = n.replace("tool:", "")
            tool_ms[tool_name] = tool_ms.get(tool_name, 0) + entry["duration_ms"]

    # 验证结果
    dps = result.get("daily_plans", [])
    spots = [s for d in dps for s in d.get("spots", [])]
    error = result.get("error_msg", "")
    has_error = bool(error and "失败" in error)
    total_budget_spent = sum(float(d.get("daily_budget", 0) or 0) for d in dps)

    ok = len(dps) == case["days"] and len(spots) > 0 and not has_error

    # 打印本轮摘要
    print(f"  [{label}] 总耗时: {total_sec:.1f}s | "
          f"{len(dps)}天 {len(spots)}景点 | 预算: ¥{total_budget_spent:.0f} | "
          f"{'✅' if ok else '❌'}")

    for node_key in REPORT_ORDER:
        if node_key in node_ms:
            print(f"    {CORE_NODES.get(node_key, node_key):20s} {node_ms[node_key]/1000:8.2f}s")

    if tool_ms:
        tools_str = " | ".join(f"{k}: {v/1000:.1f}s" for k, v in tool_ms.items())
        print(f"    工具调用: {tools_str}")

    row = {
        "case": case["name"],
        "run": run_idx + 1,
        "destination": dest,
        "total_sec": total_sec,
        "node_ms": node_ms,
        "tool_ms": tool_ms,
        "days": len(dps),
        "spots": len(spots),
        "budget_spent": total_budget_spent,
        "ok": ok,
        "error": error[:200] if error else "",
    }
    ALL_ROWS.append(row)
    return row


# ============================================================
# 聚合分析
# ============================================================
def aggregate(case_name: str) -> dict:
    """聚合某个用例下所有轮次的数据。"""
    rows = [r for r in ALL_ROWS if r["case"] == case_name]
    if not rows:
        return {}

    totals = [r["total_sec"] for r in rows]
    avg_total = round(statistics.mean(totals), 2)

    # 每节点平均耗时
    node_avgs: dict[str, float] = {}
    for nk in REPORT_ORDER:
        vals = [r["node_ms"].get(nk, 0) for r in rows]
        if vals:
            node_avgs[nk] = round(statistics.mean(vals), 2)

    # 工具平均
    all_tool_keys = set()
    for r in rows:
        all_tool_keys.update(r["tool_ms"].keys())
    tool_avgs: dict[str, float] = {}
    for tk in sorted(all_tool_keys):
        vals = [r["tool_ms"].get(tk, 0) for r in rows]
        tool_avgs[tk] = round(statistics.mean(vals), 2)

    spots_avg = round(statistics.mean([r["spots"] for r in rows]), 1)

    return {
        "case": case_name,
        "runs": len(rows),
        "avg_total_sec": avg_total,
        "min_total_sec": round(min(totals), 2),
        "max_total_sec": round(max(totals), 2),
        "node_avgs": node_avgs,
        "tool_avgs": tool_avgs,
        "spots_avg": spots_avg,
        "all_ok": all(r["ok"] for r in rows),
    }


# ============================================================
# 报告生成
# ============================================================
def generate_report(agg_results: list[dict]) -> str:
    """生成 Markdown 格式的基线报告。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model = os.getenv("LLM_MODEL", "deepseek-chat")

    # 全场景平均
    all_totals = [r["total_sec"] for r in ALL_ROWS]
    global_avg = round(statistics.mean(all_totals), 2)
    global_min = round(min(all_totals), 2)
    global_max = round(max(all_totals), 2)

    lines: list[str] = []
    lines.append("# 行程生成全链路耗时基线报告\n")
    lines.append(f"> 测试日期: {now_str} | 测试模型: {model} | "
                 f"每用例轮次: {RUNS_PER_CASE} | "
                 f"有效样本: {len(ALL_ROWS)} 轮\n")

    # ---- 一、整体基线 ----
    lines.append("---\n")
    lines.append("## 一、全场景整体基线\n")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| **全场景平均总耗时（简历用·优化前基准）** | **{global_avg:.1f}s** |")
    lines.append(f"| 最小单次耗时 | {global_min:.1f}s |")
    lines.append(f"| 最大单次耗时 | {global_max:.1f}s |")
    lines.append(f"| 标准差 | {round(statistics.stdev(all_totals), 1) if len(all_totals) > 1 else 0:.1f}s |")
    lines.append(f"| 有效样本数 | {len(ALL_ROWS)} |")
    lines.append("")

    # ---- 二、分用例耗时 ----
    lines.append("## 二、分用例耗时\n")
    lines.append("| 用例 | 均值(s) | 最小(s) | 最大(s) | 景点数(均) | 通过 |")
    lines.append("|------|---------|---------|---------|------------|------|")
    for agg in agg_results:
        ok = "✅" if agg["all_ok"] else "❌"
        lines.append(f"| {agg['case']} | {agg['avg_total_sec']:.1f} | "
                     f"{agg['min_total_sec']:.1f} | {agg['max_total_sec']:.1f} | "
                     f"{agg['spots_avg']:.1f} | {ok} |")
    lines.append("")

    # ---- 三、核心节点耗时分布 ----
    lines.append("## 三、核心节点耗时分布\n")

    # 表头
    header = "| 节点 |"
    sep = "|------|"
    for agg in agg_results:
        header += f" {agg['case']}(s) |"
        sep += "---------|"
    header += " 平均(s) | 占比(%) |"
    sep += "---------|---------|"
    lines.append(header)
    lines.append(sep)

    for nk in REPORT_ORDER:
        label = CORE_NODES.get(nk, nk)
        row = f"| {label} |"
        case_vals: list[float] = []
        for agg in agg_results:
            val_s = agg["node_avgs"].get(nk, 0) / 1000
            row += f" {val_s:.2f} |"
            case_vals.append(val_s)
        avg_s = round(statistics.mean(case_vals), 2) if case_vals else 0
        pct = round(avg_s / global_avg * 100, 1) if global_avg > 0 else 0
        row += f" {avg_s:.2f} | {pct:.1f}% |"
        lines.append(row)
    lines.append("")

    # 工具耗时
    all_tool_keys = set()
    for agg in agg_results:
        all_tool_keys.update(agg["tool_avgs"].keys())
    if all_tool_keys:
        lines.append("### 工具调用耗时\n")
        lines.append("| 工具 | 平均(ms) | 说明 |")
        lines.append("|------|----------|------|")
        for tk in sorted(all_tool_keys):
            vals = [agg["tool_avgs"].get(tk, 0) for agg in agg_results]
            avg_ms = round(statistics.mean(vals), 0)
            note = "景点检索（outline_generate 内部）" if "spot_retriever" in tk else ""
            lines.append(f"| `{tk}` | {avg_ms:.0f} | {note} |")
        lines.append("")

    # ---- 四、瓶颈定位 ----
    lines.append("## 四、瓶颈定位\n")

    # 计算排名
    node_pcts: list[tuple[str, float]] = []
    for nk in REPORT_ORDER:
        case_vals_s = [agg["node_avgs"].get(nk, 0) / 1000 for agg in agg_results]
        avg_s = round(statistics.mean(case_vals_s), 2) if case_vals_s else 0
        pct = round(avg_s / global_avg * 100, 1) if global_avg > 0 else 0
        node_pcts.append((nk, pct))

    node_pcts.sort(key=lambda x: x[1], reverse=True)

    for rank, (nk, pct) in enumerate(node_pcts[:3], 1):
        label = CORE_NODES.get(nk, nk)
        lines.append(f"### Top {rank}: {label}（{pct:.1f}%）\n")

        if nk == "daily_fill":
            lines.append("- **瓶颈类型**: 大模型串行调用（N 天 × 单日 LLM 生成）")
            lines.append("- **根因分析**: 3 天行程需要依次调用 3 次 LLM 逐日生成，")
            lines.append("  每次调用包含景点选择、预算拆分、美食/交通/住宿推荐，prompt 长度 2000+ tokens")
            lines.append("- **优化方向**: 将 3 天改为并发填充（parallel），")
            lines.append("  预计可将该节点耗时压缩至单日最长耗时级别（约降至当前的 35-50%）")
            lines.append("- **预期收益**: 总耗时降低 30-40%")
        elif nk == "outline_generate":
            lines.append("- **瓶颈类型**: 工具调用 + LLM 框架生成双阶段串行")
            lines.append("- **根因分析**: 内部包含 spot_retriever 工具调用（LLM 检索景点）")
            lines.append("  和 LLM 框架生成两次独立的大模型交互，串行累加")
            lines.append("- **优化方向**: ")
            lines.append("  1. 为热门城市预置景点缓存（本地 JSON/ChromaDB），跳过 LLM 检索")
            lines.append("  2. spot_retriever 和框架生成之间减少无效等待")
            lines.append("- **预期收益**: 热门城市该节点可降至 10-15s，总耗时降低 10-15%")
        elif nk == "fact_check":
            lines.append("- **瓶颈类型**: LLM 校验调用")
            lines.append("- **根因分析**: 逐景点逐项校验的 prompt 包含完整行程数据，token 消耗大")
            lines.append("- **优化方向**: 规则兜底优先（本地正则/字典匹配），仅在规则无法判定时回调 LLM")
            lines.append("- **预期收益**: 总耗时降低 5-10%")
        elif nk == "result_summary":
            lines.append("- **瓶颈类型**: 天气工具调用 + 输出组装")
            lines.append("- **根因分析**: 调用 weather_query 工具查询天气，但耗时通常较短")
            lines.append("- **优化方向**: 低优先级，可考虑结果缓存复用")
            lines.append("- **预期收益**: 总耗时降低 <3%")
        lines.append("")

    # ---- 优化优先级排序 ----
    lines.append("### 优化优先级排序\n")
    lines.append("| 优先级 | 节点 | 占比 | 优化方向 | 预期总耗时降幅 | 实施难度 |")
    lines.append("|--------|------|------|----------|---------------|---------|")
    lines.append(f"| **P0** | {CORE_NODES['daily_fill']} | {dict(node_pcts).get('daily_fill', 0):.1f}% | 并发填充 N 天行程 | 30-40% | 中 |")
    lines.append(f"| **P1** | {CORE_NODES['outline_generate']} | {dict(node_pcts).get('outline_generate', 0):.1f}% | 景点缓存预热 + 跳过LLM检索 | 10-15% | 低 |")
    lines.append(f"| P2 | {CORE_NODES['fact_check']} | {dict(node_pcts).get('fact_check', 0):.1f}% | 规则优先 + LLM 兜底 | 5-10% | 中 |")
    lines.append(f"| P3 | {CORE_NODES['result_summary']} | {dict(node_pcts).get('result_summary', 0):.1f}% | 结果缓存复用 | <3% | 低 |")
    lines.append("")

    # ---- 五、原始数据 ----
    lines.append("## 五、原始数据\n")
    lines.append("| # | 用例 | 轮次 | 总耗时(s) | "
             + " | ".join(f"{CORE_NODES.get(n, n)}(ms)" for n in REPORT_ORDER)
             + " | 景点 | 通过 |")
    lines.append("|---|------|------|-----------|"
             + "|".join(["------" for _ in REPORT_ORDER])
             + "|------|------|")

    for i, row in enumerate(ALL_ROWS, 1):
        node_strs = [f"{row['node_ms'].get(n, 0):.0f}" for n in REPORT_ORDER]
        lines.append(
            f"| {i} | {row['case']} | R{row['run']} | {row['total_sec']:.1f} | "
            + " | ".join(node_strs)
            + f" | {row['spots']} | {'✅' if row['ok'] else '❌'} |"
        )
    lines.append("")

    # ---- 工具调用原始数据 ----
    if all_tool_keys:
        lines.append("### 工具调用耗时明细\n")
        tool_header = "| # | 用例 |"
        tool_sep = "|---|------|"
        for tk in sorted(all_tool_keys):
            tool_header += f" {tk}(ms) |"
            tool_sep += "---------|"
        lines.append(tool_header)
        lines.append(tool_sep)
        for i, row in enumerate(ALL_ROWS, 1):
            tool_strs = [f"{row['tool_ms'].get(tk, 0):.0f}" for tk in sorted(all_tool_keys)]
            lines.append(f"| {i} | {row['case']} | " + " | ".join(tool_strs) + " |")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("  行程生成全链路耗时基线测试")
    print(f"  测试用例: {len(TEST_CASES)} 个 | 每用例: {RUNS_PER_CASE} 轮 | "
          f"总计: {len(TEST_CASES) * RUNS_PER_CASE} 轮")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    total_start = time.time()

    for case in TEST_CASES:
        for run_idx in range(RUNS_PER_CASE):
            run_one(case, run_idx)

    total_elapsed = time.time() - total_start

    # ---- 聚合 ----
    print(f"\n{'='*60}")
    print(f"  聚合分析")
    print(f"{'='*60}")

    agg_results = []
    for case in TEST_CASES:
        agg = aggregate(case["name"])
        agg_results.append(agg)
        if agg:
            ok = "✅" if agg["all_ok"] else "❌"
            print(f"  {ok} {agg['case']}: "
                  f"{agg['avg_total_sec']:.1f}s (min {agg['min_total_sec']:.1f} / max {agg['max_total_sec']:.1f}) | "
                  f"spots avg={agg['spots_avg']}")

    # ---- 生成报告 ----
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report = generate_report(agg_results)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)

    # ---- 终屏输出 ----
    all_totals = [r["total_sec"] for r in ALL_ROWS]
    global_avg = round(statistics.mean(all_totals), 2)

    print(f"\n{'='*60}")
    print(f"  测试完成")
    print(f"{'='*60}")
    print(f"  全场景平均总耗时: {global_avg:.1f}s（简历用「优化前」基准值）")
    print(f"  总测试耗时: {total_elapsed/60:.1f} 分钟")
    print(f"  报告已写入: {REPORT_PATH}")
    print(f"  有效样本: {len(ALL_ROWS)}/{len(TEST_CASES) * RUNS_PER_CASE}")

    # 检查全部通过
    all_ok = all(r["ok"] for r in ALL_ROWS)
    if all_ok:
        print(f"\n  🎉 全部 {len(ALL_ROWS)} 轮测试通过！")
    else:
        failed = [r for r in ALL_ROWS if not r["ok"]]
        print(f"\n  ❌ {len(failed)} 轮失败:")
        for f in failed:
            print(f"    - {f['case']} R{f['run']}: {f['error'][:120]}")
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
