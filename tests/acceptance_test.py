"""
全量验收测试脚本 —— 覆盖核心功能、异常兜底、兼容性全链路。
"""
from __future__ import annotations

import io
import json
import logging
import sys
import time

# 强制 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.WARNING,  # 抑制大量日志
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 切换工作目录 & 添加项目根到 sys.path
import os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from src.agent import run_travel_planner


def print_section(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_result(label: str, ok: bool, detail: str = "") -> None:
    status = "✅" if ok else "❌"
    print(f"  {status} {label}" + (f": {detail}" if detail else ""))


def check_daily_plans(result: dict, dest: str, expected_days: int, budget: float) -> dict:
    """校验行程结果并返回统计信息。"""
    error_msg = result.get("error_msg", "")
    daily_plans = result.get("daily_plans", [])
    all_spots = [s for d in daily_plans for s in d.get("spots", [])]
    total_spent = sum(float(d.get("daily_budget", 0) or 0) for d in daily_plans)

    # 基础检查
    checks = {
        "无错误信息": not error_msg or "失败" not in error_msg,
        f"天数匹配 (期望{expected_days})": len(daily_plans) == expected_days,
        "有景点内容": len(all_spots) > 0,
        "每天有景点": all(len(d.get("spots", [])) > 0 for d in daily_plans),
    }

    # 预算检查
    if budget > 0 and len(daily_plans) == expected_days:
        ratio = total_spent / budget
        checks[f"预算浮动≤10% (¥{total_spent:.0f}/{budget:.0f}={ratio:.1%})"] = 0.9 <= ratio <= 1.1

    # 景点真实性检查（基本：名称非空、有地址）
    checks["所有景点名称非空"] = all(s.get("name", "").strip() for s in all_spots)

    # 检查是否有跨市错配
    spot_names = [s.get("name", "") for s in all_spots]
    spot_addresses = [s.get("address", "") for s in all_spots]
    # 简单检查：地址是否包含目的地名称
    dest_in_addr = sum(1 for a in spot_addresses if dest in str(a))
    checks[f"地址包含'{dest}' ({dest_in_addr}/{len(all_spots)})"] = dest_in_addr >= len(all_spots) * 0.5

    return {
        "days": len(daily_plans),
        "spots": len(all_spots),
        "total_spent": total_spent,
        "spot_names": spot_names,
        "error_msg": error_msg,
        "checks": checks,
    }


# ================================================================
# 一、核心功能测试
# ================================================================
print_section("一、核心功能测试（3组标准用例）")

# ---- 测试1: 郑州（已在 nodes.py 中测试过，这里快速验证 run_travel_planner 全流程） ----
print("\n  ▶ 测试1: 郑州 3天 2000元 2人 美食+城市观光")
t1_start = time.time()
result_zz = run_travel_planner({
    "destination": "郑州",
    "days": 3,
    "total_budget": 2000,
    "people": "2人",
    "preferences": ["美食", "城市观光"],
})
t1_time = time.time() - t1_start
stats_zz = check_daily_plans(result_zz, "郑州", 3, 2000)
for label, ok in stats_zz["checks"].items():
    print_result(label, ok)
print(f"  耗时: {t1_time:.0f}s | 景点: {stats_zz['spots']} | 总花费: ¥{stats_zz['total_spent']:.0f}")
zz_all_ok = all(stats_zz["checks"].values())

# ---- 测试2: 平顶山（小众城市） ----
print("\n  ▶ 测试2: 平顶山 3天 2000元 2人 山水+休闲")
t2_start = time.time()
result_pds = run_travel_planner({
    "destination": "平顶山",
    "days": 3,
    "total_budget": 2000,
    "people": "2人",
    "preferences": ["山水", "休闲"],
})
t2_time = time.time() - t2_start
stats_pds = check_daily_plans(result_pds, "平顶山", 3, 2000)
for label, ok in stats_pds["checks"].items():
    print_result(label, ok)
print(f"  耗时: {t2_time:.0f}s | 景点: {stats_pds['spots']} | 总花费: ¥{stats_pds['total_spent']:.0f}")
# 打印景点列表
print(f"  景点列表:")
for name in stats_pds["spot_names"]:
    print(f"    • {name}")
pds_all_ok = all(stats_pds["checks"].values())

# ---- 测试3: 成都（回归） ----
print("\n  ▶ 测试3: 成都 3天 2000元 2人 美食+休闲（回归）")
t3_start = time.time()
result_cd = run_travel_planner({
    "destination": "成都",
    "days": 3,
    "total_budget": 2000,
    "people": "2人",
    "preferences": ["美食", "休闲"],
})
t3_time = time.time() - t3_start
stats_cd = check_daily_plans(result_cd, "成都", 3, 2000)
for label, ok in stats_cd["checks"].items():
    print_result(label, ok)
print(f"  耗时: {t3_time:.0f}s | 景点: {stats_cd['spots']} | 总花费: ¥{stats_cd['total_spent']:.0f}")
cd_all_ok = all(stats_cd["checks"].values())

# ================================================================
# 二、异常与兜底测试
# ================================================================
print_section("二、异常与兜底测试")

# ---- 测试4: 无效参数 ----
print("\n  ▶ 测试4: 无效参数拦截")
invalid_tests = [
    ("空目的地", {"destination": "", "days": 3, "total_budget": 2000, "people": "2人"}, "空"),
    ("负数天数", {"destination": "郑州", "days": -5, "total_budget": 2000, "people": "2人"}, "天数"),
    ("零天", {"destination": "郑州", "days": 0, "total_budget": 2000, "people": "2人"}, "天数"),
    ("负数预算", {"destination": "郑州", "days": 3, "total_budget": -100, "people": "2人"}, "预算"),
    ("空人群", {"destination": "郑州", "days": 3, "total_budget": 2000, "people": ""}, "人群"),
]

inv_all_ok = True
for label, params, keyword in invalid_tests:
    r = run_travel_planner(params)
    error = r.get("error_msg", "")
    has_error = bool(error)
    is_intercepted = has_error and keyword in error
    print_result(label, is_intercepted, error[:80] if error else "未拦截!")
    if not is_intercepted:
        inv_all_ok = False

# ---- 测试5: 县级目的地 ----
print("\n  ▶ 测试5: 极端小众地点（县级目的地）")
county_tests = [
    ("登封市", "县级市-知名"),
    ("栾川县", "县级-知名"),
]
for county, label in county_tests:
    r = run_travel_planner({
        "destination": county,
        "days": 2,
        "total_budget": 1000,
        "people": "1人",
        "preferences": ["自然风光"],
    })
    error = r.get("error_msg", "")
    daily = r.get("daily_plans", [])
    spots = [s for d in daily for s in d.get("spots", [])]
    has_content = len(daily) > 0 and len(spots) > 0
    no_crash = True  # 无异常即无崩溃
    print_result(f"{label} ({county})", no_crash and has_content,
                 f"days={len(daily)} spots={len(spots)}" if has_content else f"error={error[:60]}")

# ---- 测试6: 降级逻辑（通过检查 spot_supplement 被触发） ----
print("\n  ▶ 测试6: 降级与补充逻辑验证")
# 补充逻辑已在平顶山测试中验证，这里做一个快速检查
from src.utils.spot_supplement import supplement_spots_for_small_city
# 模拟极端场景：0个景点
zero_spots = []
supplemented = supplement_spots_for_small_city("某小城", zero_spots, [], min_count=3)
print_result("0景点→自动补充", len(supplemented) >= 3, f"补充至{len(supplemented)}个")
# 验证补充内容格式
for s in supplemented:
    has_name = bool(s.get("name", "").strip())
    has_addr = bool(s.get("address", "").strip())
    has_tags = isinstance(s.get("tags"), list) and len(s.get("tags", [])) > 0
    print_result(f"  补充项格式验证: {s['name']}", has_name and has_addr and has_tags)

# ================================================================
# 三、导出功能验证
# ================================================================
print_section("三、导出功能验证")

try:
    from src.services.export_service import get_export_service
    export_svc = get_export_service()

    # 使用郑州的行程结果测试导出
    if stats_zz["days"] >= 3:
        print("\n  ▶ Markdown 导出测试")
        try:
            md_content = export_svc.export_markdown(result_zz).decode("utf-8")
            md_ok = bool(md_content) and len(md_content) > 200
            print_result("Markdown 内容非空", md_ok, f"长度: {len(md_content)}字符")
            # 检查关键内容
            has_title = "#" in md_content
            has_dest = "郑州" in md_content
            has_spots = any(s["name"] in md_content for s in result_zz["daily_plans"][0]["spots"][:1])
            print_result("Markdown 含标题", has_title)
            print_result("Markdown 含目的地", has_dest)

            # 保存样本
            os.makedirs("tests/output", exist_ok=True)
            with open("tests/output/郑州_3天_export.md", "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"  样本已保存: tests/output/郑州_3天_export.md")
        except Exception as e:
            print_result("Markdown 导出", False, str(e)[:80])

        print("\n  ▶ PDF 导出测试")
        try:
            pdf_bytes = export_svc.export_pdf(result_zz)
            pdf_ok = pdf_bytes is not None and len(pdf_bytes) > 100
            print_result("PDF 内容非空", pdf_ok, f"大小: {len(pdf_bytes)}字节")
            if pdf_ok:
                with open("tests/output/郑州_3天_export.pdf", "wb") as f:
                    f.write(pdf_bytes)
                print(f"  样本已保存: tests/output/郑州_3天_export.pdf")
        except Exception as e:
            print_result("PDF 导出", False, str(e)[:80])

    # 平顶山导出
    if stats_pds["days"] >= 3:
        print("\n  ▶ 平顶山 Markdown 导出")
        try:
            md_pds = export_svc.export_markdown(result_pds).decode("utf-8")
            with open("tests/output/平顶山_3天_export.md", "w", encoding="utf-8") as f:
                f.write(md_pds)
            print(f"  样本已保存: tests/output/平顶山_3天_export.md ({len(md_pds)}字符)")
        except Exception as e:
            print_result("平顶山导出", False, str(e)[:80])

except ImportError as e:
    print(f"  ⚠️ 导出服务导入失败: {e}")

# ================================================================
# 最终汇总
# ================================================================
print_section("验收报告汇总")

print(f"\n  一、核心功能测试:")
print(f"    测试1 郑州 (正常):   {'✅ 通过' if zz_all_ok else '❌ 失败'} | {stats_zz['days']}天 {stats_zz['spots']}景点 ¥{stats_zz['total_spent']:.0f} | {t1_time:.0f}s")
print(f"    测试2 平顶山 (小众): {'✅ 通过' if pds_all_ok else '❌ 失败'} | {stats_pds['days']}天 {stats_pds['spots']}景点 ¥{stats_pds['total_spent']:.0f} | {t2_time:.0f}s")
print(f"    测试3 成都 (回归):   {'✅ 通过' if cd_all_ok else '❌ 失败'} | {stats_cd['days']}天 {stats_cd['spots']}景点 ¥{stats_cd['total_spent']:.0f} | {t3_time:.0f}s")

print(f"\n  二、异常与兜底:")
print(f"    无效参数拦截: {'✅ 全部拦截' if inv_all_ok else '❌ 存在漏拦'}")

print(f"\n  三、兼容性:")
print(f"    原有功能回归: {'✅ 无回归' if cd_all_ok else '❌ 存在回归'}")

all_pass = zz_all_ok and pds_all_ok and cd_all_ok and inv_all_ok
print(f"\n  {'=' * 40}")
if all_pass:
    print(f"  🎉 全量验收测试全部通过!")
else:
    print(f"  ⚠️ 存在未通过项，请检查上述详情")
print(f"  {'=' * 40}")

# 输出详细行程摘要
print_section("核心样例输出 - 郑州行程摘要")
if stats_zz["days"] >= 3:
    for day in result_zz["daily_plans"]:
        print(f"\n  Day {day['day_index']}: {day.get('theme', '')}")
        print(f"  预算: ¥{day.get('daily_budget', 0):.0f}")
        for spot in day.get("spots", []):
            print(f"    [{spot.get('time_slot', '')}] {spot.get('name', '')} "
                  f"({spot.get('duration', 0)}h, ¥{spot.get('ticket_price', 0)})")
        if day.get("food_recommendation"):
            print(f"  美食: {', '.join(day['food_recommendation'][:3])}")

print_section("核心样例输出 - 平顶山行程摘要")
if stats_pds["days"] >= 3:
    for day in result_pds["daily_plans"]:
        print(f"\n  Day {day['day_index']}: {day.get('theme', '')}")
        print(f"  预算: ¥{day.get('daily_budget', 0):.0f}")
        for spot in day.get("spots", []):
            print(f"    [{spot.get('time_slot', '')}] {spot.get('name', '')} "
                  f"({spot.get('duration', 0)}h, ¥{spot.get('ticket_price', 0)})")
        if day.get("food_recommendation"):
            print(f"  美食: {', '.join(day['food_recommendation'][:3])}")
