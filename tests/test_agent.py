import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent import run_travel_planner

if __name__ == "__main__":
    test_demand = {
        "destination": "成都",
        "days": 3,
        "total_budget": 2000,
        "people": "2个成年人",
        "preferences": ["美食", "休闲"],
        "remark": "不要早起，行程宽松一点"
    }
    
    result = run_travel_planner(test_demand)
    print("行程生成成功，总天数：", len(result.get("daily_plans", [])))
    print("第一天主题：", result["daily_plans"][0].get("theme"))
    print("Agent核心流程验证通过")