"""
快速验证脚本 —— 通过 run_travel_planner 直接测试三城市全地区检索。
"""
import io, json, logging, os, sys, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from src.agent import run_travel_planner

RESULTS = {}

def test_city(name, dest, days, budget, people, prefs):
    print(f"\n{'='*60}")
    print(f"  测试: {name} ({dest}) {days}天 ¥{budget} {people} {prefs}")
    print(f"{'='*60}")
    t0 = time.time()
    result = run_travel_planner({
        "destination": dest, "days": days,
        "total_budget": budget, "people": people,
        "preferences": prefs,
    })
    elapsed = time.time() - t0
    error = result.get("error_msg", "")
    dps = result.get("daily_plans", [])
    spots = [s for d in dps for s in d.get("spots", [])]
    total = sum(float(d.get("daily_budget", 0) or 0) for d in dps)

    checks = {
        "无错误": not error or "失败" not in error,
        f"天数={days}": len(dps) == days,
        "有景点": len(spots) > 0,
        "景点名非空": all(s.get("name", "").strip() for s in spots),
    }
    dest_in_addr = sum(1 for s in spots if dest in str(s.get("address", "")))
    if spots:
        checks[f"地址含'{dest}'"] = f"{dest_in_addr}/{len(spots)}"

    all_ok = all(v is True for v in checks.values() if isinstance(v, bool))

    for k, v in checks.items():
        print(f"  {'✅' if v is True or (isinstance(v, str) and v.startswith(f'{len(spots)}')) else '❌'} {k}: {v}")

    print(f"  耗时: {elapsed:.0f}s | {len(dps)}天 {len(spots)}景点 | 总花费: ¥{total:.0f}")
    if error:
        print(f"  ⚠️ error_msg: {error[:200]}")

    # 打印景点
    if spots:
        print(f"  景点列表:")
        for s in spots:
            print(f"    • {s.get('name','')} | {s.get('level','')} | ¥{s.get('ticket_price',0)} | {s.get('address','')[:30]}")

    RESULTS[name] = {"ok": all_ok, "dest": dest, "days": len(dps), "spots": len(spots),
                     "total": total, "elapsed": elapsed, "error": error}
    return all_ok

# ---- 测试1: 平顶山 ----
ok1 = test_city("平顶山", "平顶山", 3, 2000, "2人", ["山水", "休闲"])

# ---- 测试2: 郑州 ----
ok2 = test_city("郑州", "郑州", 3, 2000, "2人", ["美食", "城市观光"])

# ---- 测试3: 成都（回归） ----
ok3 = test_city("成都(回归)", "成都", 3, 2000, "2人", ["美食", "休闲"])

# ---- 汇总 ----
print(f"\n{'='*60}")
print(f"  测试汇总")
print(f"{'='*60}")
for name, r in RESULTS.items():
    status = "✅ 通过" if r["ok"] else "❌ 失败"
    print(f"  {status} | {name}: {r['days']}天 {r['spots']}景点 ¥{r['total']:.0f} | {r['elapsed']:.0f}s")

all_pass = all(r["ok"] for r in RESULTS.values())
print(f"\n  {'🎉 全部通过!' if all_pass else '❌ 存在失败'}")
sys.exit(0 if all_pass else 1)
