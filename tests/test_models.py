import sys
import os
# 自动将项目根目录加入Python模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.schemas.models import UserDemand, Spot, DailyPlan, TravelPlan
from src.schemas.models import UserDemand, Spot, DailyPlan, TravelPlan

if __name__ == "__main__":
    # 测试用户需求模型
    demand = UserDemand(
        destination="成都",
        days=3,
        total_budget=2000,
        people="2个成年人",
        preferences=["美食", "休闲"],
        remark="不要早起"
    )
    print("用户需求模型验证通过：", demand.model_dump())
    
    # 测试完整行程模型
    spot = Spot(
        name="宽窄巷子",
        address="成都市青羊区",
        duration=2.0,
        ticket_price=0,
        tags=["打卡", "美食"],
        recommendation="成都标志性老街，适合逛吃"
    )
    daily = DailyPlan(
        day_index=1,
        theme="老城逛吃",
        spots=[spot],
        food_recommendation=["担担面", "钟水饺"],
        traffic_note="地铁直达",
        daily_budget=300
    )
    plan = TravelPlan(
        destination="成都",
        total_days=1,
        total_budget=2000,
        people="2个成年人",
        preferences=["美食"],
        daily_plans=[daily],
        travel_tips=["带好身份证"]
    )
    print("完整行程模型验证通过：", plan.model_dump().keys())
    print("所有数据模型验证完成")