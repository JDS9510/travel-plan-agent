"""
验证 Agent 修复后的全链路测试
"""
import sys
import io
import json

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def test_all():
    # ===== TEST 1: Imports =====
    from src.agent.nodes import (
        demand_analyze_node, outline_generate_node,
        daily_fill_node, plan_check_node, result_summary_node,
    )
    from src.agent.graph import (
        travel_planner_graph, _route_after_check,
        _route_after_demand_analyze, _route_after_outline,
    )
    from src.agent import run_travel_planner
    from src.schemas.models import DailyPlan, TravelPlan

    print("=" * 60)
    print("TEST 1: Import & graph structure")
    graph = travel_planner_graph
    print(f"  [PASS] Graph compiled: {type(graph).__name__}")

    # ===== TEST 2: Error routing =====
    print()
    print("=" * 60)
    print("TEST 2: Error routing logic")

    r = _route_after_demand_analyze({"current_step": "error", "error_msg": "x"})
    assert r == "result_summary", f"Expected result_summary, got {r}"
    print(f"  [PASS] demand_analyze error -> {r}")

    r = _route_after_demand_analyze({"current_step": "parse_demand", "error_msg": ""})
    assert r == "outline_generate", f"Expected outline_generate, got {r}"
    print(f"  [PASS] demand_analyze ok -> {r}")

    r = _route_after_outline({"current_step": "error", "error_msg": "no spots"})
    assert r == "result_summary"
    print(f"  [PASS] outline error -> {r}")

    r = _route_after_outline({"current_step": "build_outline", "error_msg": ""})
    assert r == "daily_fill"
    print(f"  [PASS] outline ok -> {r}")

    r = _route_after_check({"check_result": {"is_pass": True}, "iteration_count": 0, "error_msg": ""})
    assert r == "result_summary"
    print(f"  [PASS] check(pass) -> {r}")

    from src.agent.react_revise import is_react_mode
    not_pass_expected = "react_revise" if is_react_mode() else "daily_fill"
    r = _route_after_check({"check_result": {"is_pass": False}, "iteration_count": 1, "error_msg": ""})
    assert r == not_pass_expected, f"Expected {not_pass_expected}, got {r}"
    print(f"  [PASS] check(not pass, iter=1) -> {r}")

    r = _route_after_check({"check_result": {"is_pass": False}, "iteration_count": 3, "error_msg": ""})
    assert r == "result_summary"
    print(f"  [PASS] check(not pass, iter=3) -> {r}")

    r = _route_after_check({"check_result": {}, "iteration_count": 0, "error_msg": "boom"})
    assert r == "result_summary"
    print(f"  [PASS] check(error_msg) -> {r}")

    # ===== TEST 3: Node exception handling =====
    print()
    print("=" * 60)
    print("TEST 3: Node exception resilience")

    r = demand_analyze_node({})
    print(f"  [PASS] demand_analyze(empty) step={r['current_step']}, err={str(r.get('error_msg',''))[:50]}")

    r = outline_generate_node({
        "user_demand": {"destination": "火星", "days": 3, "people": "test"},
        "travel_outline": {}, "daily_plans": [], "check_result": {},
        "iteration_count": 0, "current_step": "", "error_msg": "",
    })
    print(f"  [PASS] outline_generate(unknown) step={r['current_step']}")

    # ===== TEST 4: daily_fill_node fallback =====
    print()
    print("=" * 60)
    print("TEST 4: daily_fill_node fallback (no LLM)")

    spot_pool = [
        {"name": "景点A", "address": "成都武侯区", "duration": 2.0, "ticket_price": 50,
         "tags": ["人文", "打卡"], "recommendation": "不错"},
        {"name": "景点B", "address": "成都锦江区", "duration": 1.5, "ticket_price": 0,
         "tags": ["美食", "休闲"], "recommendation": "好吃"},
        {"name": "景点C", "address": "成都青羊区", "duration": 3.0, "ticket_price": 55,
         "tags": ["亲子", "自然"], "recommendation": "好玩"},
        {"name": "景点D", "address": "成都成华区", "duration": 2.5, "ticket_price": 70,
         "tags": ["人文", "亲子"], "recommendation": "值得去"},
        {"name": "景点E", "address": "成都金牛区", "duration": 1.0, "ticket_price": 0,
         "tags": ["休闲", "美食"], "recommendation": "惬意"},
        {"name": "景点F", "address": "成都武侯区", "duration": 2.0, "ticket_price": 30,
         "tags": ["打卡", "人文"], "recommendation": "经典"},
    ]

    daily_frameworks = [
        {"day_index": 1, "theme": "文化探索日", "budget": 500,
         "prefer_tags": ["人文", "打卡"], "food_style": "川菜"},
        {"day_index": 2, "theme": "亲子欢乐日", "budget": 400,
         "prefer_tags": ["亲子", "自然"], "food_style": "小吃"},
        {"day_index": 3, "theme": "悠闲逛吃日", "budget": 300,
         "prefer_tags": ["美食", "休闲"], "food_style": "火锅"},
    ]

    fill_state = {
        "user_demand": {"destination": "成都", "days": 3, "total_budget": 1200,
                        "people": "一家三口", "preferences": ["亲子", "美食"], "remark": ""},
        "travel_outline": {"daily_frameworks": daily_frameworks,
                           "_spot_pool": spot_pool, "total_days": 3},
        "daily_plans": [], "check_result": {},
        "iteration_count": 0, "current_step": "", "error_msg": "",
    }

    fill_result = daily_fill_node(fill_state)
    daily_plans = fill_result["daily_plans"]
    print(f"  [PASS] Generated {len(daily_plans)} daily plans")

    # CRITICAL
    assert len(daily_plans) == 3, f"FAIL: expected 3, got {len(daily_plans)}"
    print(f"  [PASS] CRITICAL: daily_plans count == 3")

    for i, plan in enumerate(daily_plans):
        assert plan.get("day_index"), f"Missing day_index in plan {i}"
        assert plan.get("theme"), f"Missing theme in plan {i}"
        assert plan.get("spots"), f"Missing spots in plan {i}"
        assert len(plan["spots"]) >= 1, f"Plan {i} has 0 spots"
        assert plan.get("daily_budget") is not None
        assert plan.get("food_recommendation")
        assert plan.get("traffic_note")
        print(f"  Day {plan['day_index']}: {plan['theme']} ({len(plan['spots'])} spots, {plan['daily_budget']} yuan)")

    # Pydantic validation
    for i, plan_dict in enumerate(daily_plans):
        try:
            DailyPlan.model_validate(plan_dict)
            print(f"  [PASS] Day {i+1} Pydantic validation OK")
        except Exception as e:
            print(f"  [FAIL] Day {i+1} Pydantic: {e}")
            raise

    # ===== TEST 5: Full pipeline =====
    print()
    print("=" * 60)
    print("TEST 5: Full pipeline (3-day Chengdu)")

    result = run_travel_planner({
        "destination": "成都",
        "days": 3,
        "total_budget": 3000,
        "people": "一家三口（父母+8岁孩子）",
        "preferences": ["亲子", "美食", "人文"],
        "remark": "不要太赶",
    })

    error_msg = result.get("error_msg", "")
    if error_msg:
        print(f"  error: {error_msg[:120]}")

    outline = result.get("travel_outline", {})
    data = outline.get("final_result", {})
    if not data:
        data = {
            "destination": result.get("user_demand", {}).get("destination", ""),
            "total_days": len(result.get("daily_plans", [])),
            "total_budget": result.get("user_demand", {}).get("total_budget", 0),
            "people": result.get("user_demand", {}).get("people", ""),
            "preferences": result.get("user_demand", {}).get("preferences", []),
            "daily_plans": result.get("daily_plans", []),
            "travel_tips": [],
            "iteration_count": result.get("iteration_count", 0),
        }
    if data:
        plans = data.get("daily_plans", [])
        print(f"  destination: {data.get('destination')}")
        print(f"  total_days: {data.get('total_days')}")
        print(f"  daily_plans count: {len(plans)}")
        print(f"  travel_tips: {len(data.get('travel_tips', []))}")
        print(f"  iteration_count: {data.get('iteration_count')}")

        assert len(plans) == 3, f"FAIL: daily_plans={len(plans)}, expected 3"
        print(f"  [PASS] CRITICAL: daily_plans length == 3")

        for plan in plans:
            print(f"    Day {plan['day_index']}: {plan['theme']} ({len(plan['spots'])} spots)")

    # ===== TEST 6: TravelPlan validation =====
    print()
    print("=" * 60)
    print("TEST 6: Full TravelPlan Pydantic validation")

    if data and data.get("daily_plans"):
        try:
            tp = TravelPlan.model_validate({
                "destination": data["destination"],
                "total_days": len(data["daily_plans"]),
                "total_budget": data.get("total_budget", 0),
                "people": data.get("people", ""),
                "preferences": data.get("preferences", []),
                "daily_plans": data["daily_plans"],
                "travel_tips": data.get("travel_tips", []),
            })
            print(f"  [PASS] TravelPlan validated OK")
            print(f"  Keys: {list(tp.model_dump().keys())}")
        except Exception as e:
            print(f"  [FAIL] TravelPlan: {e}")
            raise

    # ===== TEST 7: Error handling =====
    print()
    print("=" * 60)
    print("TEST 7: Error handling for invalid input")

    bad_result = run_travel_planner({
        "destination": "火星", "days": 3, "people": "外星人"
    })
    print(f"  error_msg: {(bad_result.get('error_msg') or '')[:100]}")

    # ===== TEST 8: Revision loop =====
    print()
    print("=" * 60)
    print("TEST 8: Revision loop (daily_fill with previous check_result)")

    rev_state = dict(fill_state)
    rev_state["daily_plans"] = daily_plans
    rev_state["check_result"] = {
        "is_pass": False,
        "issues": ["第1天安排了太多景点"],
        "suggestions": ["第1天减少1个景点"],
    }
    rev_state["iteration_count"] = 1
    rev_result = daily_fill_node(rev_state)
    rev_plans = rev_result["daily_plans"]
    print(f"  [PASS] Revision: generated {len(rev_plans)} plans, iter={rev_result['iteration_count']}, step={rev_result['current_step']}")
    assert len(rev_plans) == 3, f"FAIL: revision plans count not 3"
    assert rev_result["iteration_count"] == 2
    print(f"  [PASS] Revision iteration_count == 2")

    print()
    print("=" * 60)
    print("ALL 8 TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
