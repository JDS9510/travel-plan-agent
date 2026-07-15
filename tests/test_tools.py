import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.spot_retriever import spot_retriever
from src.tools.budget_calculator import budget_calculator

if __name__ == "__main__":
    # 测试景点检索工具
    spots = spot_retriever.invoke({"destination": "成都", "tags": ["美食", "休闲"]})
    print("景点检索成功，返回数量：", len(spots))
    
    # 测试预算统计工具
    test_plans = [
        {"day_index": 1, "daily_budget": 500},
        {"day_index": 2, "daily_budget": 600}
    ]
    budget_result = budget_calculator.invoke({"daily_plans": test_plans, "total_budget_limit": 2000})
    print("预算统计结果：", budget_result)
    print("所有工具基础验证通过")