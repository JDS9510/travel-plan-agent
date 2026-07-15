"""通过 API 端点验证全地区检索 — 使用 Python urllib 避免 Windows 编码问题。"""
import io, json, sys, time, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"

def api_call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json") if data else None
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def test_api(dest, days, budget, people, prefs):
    print(f"\n{'='*60}")
    print(f"  API测试: {dest} {days}天 ¥{budget} {prefs}")
    print(f"{'='*60}")

    # 1. 提交任务
    t0 = time.time()
    status, resp = api_call("POST", "/api/travel/generate-async", {
        "destination": dest, "days": days, "total_budget": budget,
        "people": people, "preferences": prefs,
    })
    print(f"  提交: HTTP {status} | {json.dumps(resp, ensure_ascii=False)[:200]}")

    if status != 200:
        print(f"  ❌ 提交失败")
        return False

    task_id = resp.get("task_id", "")
    if not task_id:
        print(f"  ❌ 无 task_id")
        return False

    # 2. 轮询状态
    for _ in range(60):
        time.sleep(5)
        status, resp = api_call("GET", f"/api/travel/task/{task_id}")
        if resp.get("status") in ("completed", "failed"):
            break

    elapsed = time.time() - t0
    print(f"  最终状态: {resp.get('status')} | 耗时: {elapsed:.0f}s")

    result = resp.get("result", {})
    error = result.get("error_msg", "")
    dps = result.get("daily_plans", [])
    spots = [s for d in dps for s in d.get("spots", [])]

    if error:
        print(f"  error_msg: {error[:200]}")

    dest_in_addr = sum(1 for s in spots if dest in str(s.get("address", "")))
    print(f"  结果: {len(dps)}天 {len(spots)}景点 | 地址含'{dest}': {dest_in_addr}/{len(spots)}")

    ok = len(dps) == days and len(spots) > 0 and not ("失败" in error)
    print(f"  {'✅ 通过' if ok else '❌ 失败'}")

    if spots:
        print(f"  景点列表:")
        for s in spots:
            print(f"    • {s.get('name','')} | {s.get('level','')} | ¥{s.get('ticket_price',0)} | {s.get('address','')[:30]}")

    return ok

# 测试平顶山（API）
ok_api = test_api("平顶山", 3, 2000, "2人", ["山水", "休闲"])

print(f"\n{'='*60}")
print(f"  API测试: {'✅ 通过' if ok_api else '❌ 失败'}")
print(f"{'='*60}")
