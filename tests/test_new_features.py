"""
验证脚本 —— 检查所有新增功能的导入与基础逻辑。
"""
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    errors = []

    print("=== 1. Basic Import Check ===")
    checks = [
        ("src.agent.state", "TravelState"),
        ("src.agent.react_revise", "react_revise_node, is_react_mode"),
        ("src.agent", "run_travel_planner"),
        ("src.services.task_service", "TaskService, TaskStatus, TaskInfo, get_task_service"),
        ("src.services.cache_service", "CacheService, get_cache_service"),
        ("src.services", "get_task_service, get_cache_service"),
        ("src.api.schemas", "TravelPlanRequest, ApiResponse, AsyncTaskResponse, TaskStatusResponse"),
        ("src.api", "app"),
        ("src.utils", "LLMOutputValidator, AgentTracer, get_tracer"),
        ("src.tools", "spot_retriever, budget_calculator, plan_checker, weather_query"),
        ("src.schemas", "Spot, DailyPlan, TravelPlan, UserDemand, CheckOutput, OutlineOutput"),
        ("src.rag", "VectorStore, get_vector_store"),
        ("src.llm", "LLMClient, get_llm_client, llm_client"),
    ]

    for module_name, attrs in checks:
        try:
            mod = __import__(module_name, fromlist=[attrs.split(",")[0].strip()])
            for attr in attrs.split(","):
                getattr(mod, attr.strip())
            print(f"  [OK] {module_name}")
        except Exception as e:
            msg = f"  [FAIL] {module_name}: {e}"
            errors.append(msg)
            print(msg)

    print("\n=== 2. State Field Integrity ===")
    from src.agent.state import TravelState
    required_keys = [
        'user_demand', 'travel_outline', 'daily_plans', 'check_result',
        'iteration_count', 'current_step', 'error_msg', 'react_trace', 'run_mode'
    ]
    for key in required_keys:
        present = key in TravelState.__annotations__
        status = "OK" if present else "MISSING"
        if not present:
            errors.append(f"State field {key} missing")
        print(f"  [{status}] {key}")

    print("\n=== 3. Cache Service Test ===")
    from src.services.cache_service import get_cache_service
    cache = get_cache_service()
    print(f"  enabled={cache.enabled}, size={cache.size}")

    demand1 = {'destination': 'Chengdu', 'days': 3, 'total_budget': 3000, 'people': 'test', 'preferences': ['food']}
    demand2 = {'destination': 'Chengdu', 'days': 3, 'total_budget': 3000, 'people': 'test', 'preferences': ['food']}
    demand3 = {'destination': 'Hangzhou', 'days': 2, 'total_budget': 2000, 'people': 'couple', 'preferences': ['romance']}

    r1 = cache.get(demand1)
    print(f"  First query: {'HIT (unexpected)' if r1 else 'MISS (expected)'}")
    cache.set(demand1, {'test': 'cached'})
    r2 = cache.get(demand2)
    print(f"  Second query (same params): {'HIT (expected)' if r2 else 'MISS (unexpected)'}")
    r3 = cache.get(demand3)
    print(f"  Different params: {'HIT (unexpected)' if r3 else 'MISS (expected)'}")
    print(f"  Stats: {cache.stats}")

    if r1 is not None:
        errors.append("Cache: first query should be miss but got hit")
    if r2 is None:
        errors.append("Cache: second query should be hit but got miss")
    if r3 is not None:
        errors.append("Cache: different params should be miss but got hit")

    print("\n=== 4. ReAct Mode Detection ===")
    from src.agent.react_revise import is_react_mode
    mode = os.getenv("TRAVEL_PLANNER_MODE", "react")
    print(f"  TRAVEL_PLANNER_MODE={mode}, is_react_mode={is_react_mode()}")
    expected = (mode == "react")
    if is_react_mode() != expected:
        errors.append(f"ReAct mode mismatch: env={mode}, is_react_mode={is_react_mode()}")

    print("\n=== 5. Graph Build Test ===")
    try:
        from src.agent.graph import travel_planner_graph
        node_names = list(travel_planner_graph.nodes.keys()) if hasattr(travel_planner_graph, 'nodes') else "N/A"
        print(f"  Graph compiled, nodes={node_names}")
    except Exception as e:
        msg = f"Graph build failed: {e}"
        errors.append(msg)
        print(f"  [FAIL] {msg}")

    print("\n=== 6. Pydantic Schema Validation ===")
    try:
        from src.api.schemas import AsyncTaskResponse, TaskStatusResponse, TravelPlanRequest, ApiResponse
        req = TravelPlanRequest(destination="Chengdu", days=3, total_budget=3000, people="test")
        print(f"  TravelPlanRequest: {req.model_dump()['destination']}")

        async_resp = AsyncTaskResponse(code=200, msg="ok", task_id="abc123")
        print(f"  AsyncTaskResponse: task_id={async_resp.task_id}")

        task_resp = TaskStatusResponse(code=200, task_id="abc123", status="running", progress="generating...")
        print(f"  TaskStatusResponse: status={task_resp.status}")

        api_resp = ApiResponse(code=200, msg="success", data={"key": "value"})
        print(f"  ApiResponse: code={api_resp.code}")
        print("  [OK] All Pydantic schemas valid")
    except Exception as e:
        msg = f"Schema validation failed: {e}"
        errors.append(msg)
        print(f"  [FAIL] {msg}")

    print("\n=== 7. Agent Init New Fields ===")
    try:
        from src.agent import run_travel_planner
        # Just check the init code path (no LLM call needed)
        import inspect
        src = inspect.getsource(run_travel_planner)
        has_react_trace = 'react_trace' in src
        has_run_mode = 'run_mode' in src
        print(f"  react_trace init: {'OK' if has_react_trace else 'MISSING'}")
        print(f"  run_mode init: {'OK' if has_run_mode else 'MISSING'}")
        if not has_react_trace:
            errors.append("run_travel_planner missing react_trace init")
        if not has_run_mode:
            errors.append("run_travel_planner missing run_mode init")
    except Exception as e:
        msg = f"Agent init check failed: {e}"
        errors.append(msg)
        print(f"  [FAIL] {msg}")

    print(f"\n{'='*50}")
    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
