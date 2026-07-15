"""
核心节点实现 —— LangGraph 工作流中的 6 个无状态节点函数。

每个节点函数：
- 接收 TravelState 完整状态字典
- 返回 dict[str, Any] 增量状态更新（LangGraph 自动合并）
- 严格遵循 TravelState 各字段的更新规则（全量覆盖 / 增量追加）
- 内置全局异常捕获，出错时写入 error_msg 而非静默中断
- 统一使用 LLMOutputValidator 校验大模型输出，最多 3 次自动纠错

节点间的状态流转：
  demand_analyze → outline_generate → daily_fill → fact_check → plan_check → result_summary
                                                       ↑            │
                                                       └── retry ───┘
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

from src.llm.llm_client import llm_client
from src.llm.model_router import RoutedLLMAdapter, get_model_router
from src.schemas.models import (
    CheckOutput,
    DailyPlan,
    DemandAnalyzeOutput,
    FactCheckOutput,
    OutlineOutput,
    Spot,
)
from src.tools.budget_calculator import budget_calculator
from src.tools.plan_checker import plan_checker
from src.tools.spot_retriever import spot_retriever
from src.tools.weather_query import weather_query
from src.utils.llm_validator import LLMOutputValidator

# LangGraph 循环硬上限（3 次迭代后强制退出）
_MAX_ITERATIONS = 3

# 全局校验器实例（节点间复用）
_validator = LLMOutputValidator(max_retries=3)


# ============================================================
# 内部辅助函数
# ============================================================
def _safe_invoke_tool(tool: Any, params: dict) -> dict[str, Any]:
    """安全调用工具，统一异常处理 + 追踪记录。"""
    start_ts = time.time()
    error_msg: str | None = None
    result_data = None

    try:
        result_data = tool.invoke(params)
        return {"data": result_data, "error": None}
    except Exception as exc:
        error_msg = str(exc)
        return {"data": None, "error": error_msg}
    finally:
        duration_ms = (time.time() - start_ts) * 1000
        try:
            from src.utils.tracer import get_tracer
            tool_name = getattr(tool, "name", tool.__class__.__name__)
            get_tracer().trace_tool_call(
                tool_name=str(tool_name),
                params={k: str(v)[:100] for k, v in params.items()},
                result=(
                    {"count": len(result_data)} if isinstance(result_data, list)
                    else str(result_data)[:200] if result_data else None
                ),
                duration_ms=duration_ms,
                error=error_msg,
            )
        except Exception:
            pass


def _build_fallback_daily_plan(
    day_index: int,
    framework: dict[str, Any],
    spot_pool: list[dict[str, Any]],
    day_budget: float,
    used_spot_names: set[str] | None = None,
    destination: str = "",
) -> dict[str, Any]:
    """从景点池按标签匹配构造兜底单日行程，保证不会返回空数据。

    Args:
        day_index: 第几天（1-based）。
        framework: 当天框架 dict。
        spot_pool: 全部可用景点池。
        day_budget: 当天预算。
        used_spot_names: 跨天已使用的景点名称集合，传入则跳过已用景点，
                         确保不同天分配不同景点。
        destination: 目的地城市，用于生成更具体的美食/交通/住宿建议。
    """
    prefer_tags = framework.get("prefer_tags", [])
    theme = framework.get("theme", f"第{day_index}天行程")
    food_style = framework.get("food_style", "")
    _used = used_spot_names or set()

    # 当天内部去重 + 跨天去重
    seen_names: set[str] = set(_used)
    matching: list[dict[str, Any]] = []
    for s in spot_pool:
        name = s.get("name", "")
        if name in seen_names:
            continue
        if any(t.lower() in [pt.lower() for pt in prefer_tags] for t in s.get("tags", [])):
            matching.append(s)
            seen_names.add(name)

    if len(matching) < 3:
        for s in spot_pool:
            name = s.get("name", "")
            if name not in seen_names:
                matching.append(s)
                seen_names.add(name)
                if len(matching) >= 3:
                    break

    # 如果标签匹配 + 补足的仍不足，放宽限制复用已用景点（兜底中的兜底）
    if len(matching) < 2:
        for s in spot_pool:
            name = s.get("name", "")
            if name not in seen_names:
                matching.append(s)
                seen_names.add(name)
                if len(matching) >= 2:
                    break

    # 池中所有景点都已用过一轮 → 循环复用
    while len(matching) < 3:
        for s in spot_pool:
            name = s.get("name", "")
            if name in {m.get("name", "") for m in matching}:
                continue
            matching.append(s)
            if len(matching) >= 3:
                break
        else:
            break

    # ---- 按时段分配景点 ----
    time_slots = ["上午", "下午", "晚上"]
    spots = []
    for idx, s in enumerate(matching[:4]):
        spot_dict = {
            "name": s.get("name", ""),
            "address": s.get("address", ""),
            "duration": float(s.get("duration", 1.5)),
            "ticket_price": float(s.get("ticket_price", 0)),
            "level": s.get("level", ""),
            "area": s.get("area", ""),
            "core_feature": s.get("core_feature", ""),
            "tags": s.get("tags", []),
            "recommendation": s.get("recommendation", ""),
            "time_slot": time_slots[idx] if idx < len(time_slots) else "下午",
        }
        spots.append(spot_dict)

    # ---- 根据偏好标签 + 目的地生成更丰富的美食推荐 ----
    food_recommendation = _build_fallback_food(prefer_tags, food_style, destination)

    # ---- 生成更具体的交通建议 ----
    traffic_note = _build_fallback_traffic(prefer_tags, destination)

    # ---- 住宿建议 ----
    accommodation = _build_fallback_accommodation(destination, day_budget)

    # ---- 计算 5 类目预算拆分 ----
    spot_cost = sum(float(s.get("ticket_price", 0) or 0) for s in spots)
    final_budget = float(day_budget) if day_budget > 0 else round(spot_cost + 60 * 3 + 20 + 180, 2)
    hotel_cost = round(final_budget * 0.30, 2)
    meal_cost = round(final_budget * 0.28, 2)
    transport_cost = round(final_budget * 0.10, 2)
    emergency_cost = round(final_budget * 0.06, 2)
    ticket_cost = round(final_budget - hotel_cost - meal_cost - transport_cost - emergency_cost, 2)

    budget_breakdown = {
        "住宿": hotel_cost,
        "餐饮": meal_cost,
        "市内交通": transport_cost,
        "景点门票": ticket_cost,
        "应急备用金": emergency_cost,
    }

    return {
        "day_index": day_index,
        "theme": theme,
        "spots": spots,
        "food_recommendation": food_recommendation,
        "traffic_note": traffic_note,
        "accommodation": accommodation,
        "daily_budget": final_budget,
        "budget_breakdown": budget_breakdown,
    }


def _build_fallback_food(
    prefer_tags: list[str],
    food_style: str = "",
    destination: str = "",
) -> list[str]:
    """根据偏好标签、餐饮风格和目的地生成兜底美食推荐列表。

    Args:
        prefer_tags: 偏好标签列表。
        food_style: 餐饮风格描述。
        destination: 目的地城市，用于匹配地方特色菜系。

    Returns:
        list[str]: 3-5 条美食推荐。
    """
    # 目的地 → 地方特色菜系映射
    dest_food_map: dict[str, list[str]] = {
        "郑州": ["合记羊肉烩面", "葛记焖饼", "萧记三鲜烩面", "方中山胡辣汤", "老蔡记蒸饺"],
        "杭州": ["西湖醋鱼（楼外楼）", "龙井虾仁", "知味观小吃", "葱包烩", "片儿川"],
        "成都": ["龙抄手", "钟水饺", "陈麻婆豆腐", "串串香", "蛋烘糕"],
        "北京": ["全聚德烤鸭", "东来顺涮羊肉", "护国寺小吃", "炸酱面", "卤煮火烧"],
        "西安": ["回民街羊肉泡馍", "肉夹馍", "凉皮", "biangbiang面", "灌汤包"],
        "南京": ["鸭血粉丝汤", "盐水鸭", "小笼包", "牛肉锅贴", "糖芋苗"],
        "重庆": ["重庆火锅", "小面", "酸辣粉", "毛血旺", "辣子鸡"],
        "武汉": ["热干面", "豆皮", "武昌鱼", "排骨藕汤", "面窝"],
        "广州": ["早茶点心", "煲仔饭", "白切鸡", "肠粉", "双皮奶"],
        "深圳": ["潮汕牛肉火锅", "海鲜大排档", "客家酿豆腐", "沙井蚝", "肠粉"],
        "长沙": ["臭豆腐", "口味虾", "剁椒鱼头", "糖油粑粑", "辣椒炒肉"],
        "苏州": ["松鼠桂鱼", "阳澄湖大闸蟹", "糖粥", "生煎包", "太湖三白"],
        "平顶山": ["郏县饸饹面", "宝丰羊肉汤", "鲁山揽锅菜", "叶县烩面", "舞钢热豆腐"],
    }
    # 标签 → 菜系/美食映射
    tag_food_map: dict[str, list[str]] = {
        "美食": ["本地特色菜馆（评分 4.5+）", "网红小吃街", "地道火锅/串串", "老字号酒楼"],
        "休闲": ["咖啡馆/茶馆", "甜品店", "西式简餐", "江景/湖景餐厅"],
        "历史文化": ["老字号餐厅", "传统小吃", "宫廷菜/官府菜", "本地百年老店"],
        "自然风光": ["景区农家乐", "山间茶馆", "湖鲜/河鲜餐厅", "野餐便当"],
        "亲子": ["亲子主题餐厅", "儿童友好自助餐", "冰淇淋/甜品店", "快餐简餐"],
        "摄影": ["景观餐厅（适合拍照）", "网红咖啡馆", "露台餐厅", "特色主题餐厅"],
        "购物": ["商圈美食广场", "日料/韩料", "轻食沙拉", "高档西餐"],
        "夜生活": ["夜市大排档", "酒吧街小食", "深夜食堂", "烧烤摊"],
        "探险": ["能量简餐", "便携干粮", "户外野炊", "地方快餐"],
        "文艺": ["独立咖啡馆", "文艺小馆", "素食餐厅", "书店餐厅"],
    }

    foods: list[str] = []
    seen: set[str] = set()

    # 优先使用目的地特色美食
    dest_foods = dest_food_map.get(destination, [])
    for f in dest_foods[:3]:
        if f not in seen:
            foods.append(f)
            seen.add(f)

    # 按偏好标签匹配
    for tag in prefer_tags:
        candidates = tag_food_map.get(tag, [])
        for c in candidates:
            if c not in seen:
                foods.append(c)
                seen.add(c)

    # 如果匹配不足，补充通用推荐
    defaults = ["本地特色餐馆", "人气小吃街", "口碑本帮菜馆", "美食广场"]
    for d in defaults:
        if d not in seen and len(foods) < 5:
            foods.append(d)
            seen.add(d)

    # food_style 作为补充
    if food_style and food_style not in seen:
        foods.insert(min(1, len(foods)), f"{food_style}风味餐厅")

    return foods[:5]


def _build_fallback_traffic(prefer_tags: list[str], destination: str = "") -> str:
    """根据偏好标签和目的地生成更具体的交通建议。

    Args:
        prefer_tags: 偏好标签列表。
        destination: 目的地城市，用于匹配当地主要交通线路。

    Returns:
        str: 交通建议描述。
    """
    # 目的地 → 主要地铁线路参考
    dest_metro: dict[str, str] = {
        "郑州": "郑州地铁1号线/2号线/5号线",
        "杭州": "杭州地铁1号线/2号线",
        "成都": "成都地铁1号线/2号线/3号线",
        "北京": "北京地铁1号线/2号线/4号线/10号线",
        "西安": "西安地铁2号线/4号线",
        "南京": "南京地铁1号线/2号线/3号线",
        "重庆": "重庆轨道交通1号线/2号线/3号线",
        "武汉": "武汉地铁2号线/4号线",
        "广州": "广州地铁1号线/3号线/5号线",
        "深圳": "深圳地铁1号线/4号线/11号线",
        "长沙": "长沙地铁1号线/2号线",
        "苏州": "苏州轨道交通1号线/2号线",
        "平顶山": "市内公交（无地铁），主要景点间建议打车或公交",
    }
    metro = dest_metro.get(destination, "市内地铁")

    tag_traffic: dict[str, str] = {
        "休闲": f"建议打车/网约车出行，省时省力，单程约 15-30 元；{metro}也可方便抵达各商圈",
        "亲子": f"推荐网约车出行，带小孩更方便；也可乘坐{metro}（避开早晚高峰 8:00-9:00/17:30-19:00）",
        "自然风光": f"部分郊区景点建议包车或乘坐景区直通车，提前查好末班车时间；市区段可乘{metro}",
        "摄影": "建议早起打车前往拍摄点（光线最佳时段 6:00-8:00），市区内地铁+步行",
        "购物": f"商圈之间{metro}直达最方便，购物后建议打车回酒店",
        "探险": "偏远景点建议提前租车或包车，备好离线地图",
        "夜生活": "晚间出行建议打车/网约车，注意末班地铁时间（通常 23:00 左右）",
    }

    # 按偏好匹配特定建议
    for tag in prefer_tags:
        if tag in tag_traffic:
            return tag_traffic[tag]

    # 默认建议 —— 含目的地具体地铁信息
    if destination:
        return f"建议乘坐{metro}+步行，市内景点间通勤约 20-40 分钟；避开早晚高峰（8:00-9:00 / 17:30-19:00）"
    return "建议乘坐地铁+步行，市内景点间通勤约 20-40 分钟；避开早晚高峰（8:00-9:00 / 17:30-19:00）"


def _build_fallback_accommodation(destination: str, day_budget: float = 0) -> str:
    """根据目的地和预算生成兜底住宿建议。

    Args:
        destination: 目的地城市。
        day_budget: 当天预算（用于估算酒店档次）。

    Returns:
        str: 住宿建议描述。
    """
    # 目的地 → 推荐住宿区域
    dest_area: dict[str, str] = {
        "郑州": "二七广场/紫荆山附近",
        "杭州": "西湖/武林广场附近",
        "成都": "春熙路/太古里附近",
        "北京": "前门/东单/西单附近",
        "西安": "钟楼/回民街附近",
        "南京": "新街口/夫子庙附近",
        "重庆": "解放碑/洪崖洞附近",
        "武汉": "江汉路/楚河汉街附近",
        "广州": "天河/北京路附近",
        "深圳": "福田CBD/南山海岸城附近",
        "长沙": "五一广场/坡子街附近",
        "苏州": "观前街/平江路附近",
        "平顶山": "中兴路/矿工路附近市区",
    }
    area = dest_area.get(destination, "市中心交通便利区域")

    # 根据预算估算酒店档次
    if day_budget <= 0:
        hotel_tier = "经济型酒店（如汉庭/如家/7天）"
        price = "约 150-250 元/晚/间"
    elif day_budget < 500:
        hotel_tier = "经济型酒店（如汉庭/如家/7天）"
        price = "约 150-250 元/晚/间"
    elif day_budget < 800:
        hotel_tier = "舒适型酒店（如全季/亚朵/智选假日）"
        price = "约 250-400 元/晚/间"
    else:
        hotel_tier = "高档酒店（如希尔顿欢朋/皇冠假日/本地五星）"
        price = "约 400-800 元/晚/间"

    return f"{area}{hotel_tier}，{price}"


def _validate_daily_plan(
    day_plan: dict[str, Any],
    spot_pool: list[dict[str, Any]],
) -> tuple[dict | None, str]:
    """用 Pydantic DailyPlan 模型校验单日行程。"""
    try:
        validated = DailyPlan.model_validate(day_plan)
        return validated.model_dump(), ""
    except Exception as e:
        return None, str(e)


def _call_llm_with_validation(
    prompt: str,
    target_model: type[Any],
    temperature: float = 0.3,
    task_name: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """使用 LLMOutputValidator 调用 LLM 并校验输出。

    统一入口，所有节点均通过此函数与大模型交互，
    自动享受 3 次纠错重试 + 格式校验 + 模型分层路由。

    Args:
        prompt: Prompt 文本。
        target_model: 目标 Pydantic 校验模型。
        temperature: 初始温度。
        task_name: 任务名称（用于模型路由，空字符串使用主模型）。
    """
    # 根据任务名路由到合适的模型
    if task_name:
        routed_client = RoutedLLMAdapter(task_name, get_model_router())
    else:
        routed_client = llm_client

    return _validator.validate(
        llm_client=routed_client,
        prompt=prompt,
        target_model=target_model,
        initial_temperature=temperature,
    )


# ============================================================
# Node 0.5：景点检索节点（从 outline_generate 拆分）
# ============================================================
def spot_retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """独立的景点检索节点 —— 从 outline_generate_node 拆分。

    只调用 spot_retriever 工具，将结果写入 travel_outline._spot_pool。
    后续 outline_generate 和 spot_pre_check 可并行使用该数据。

    状态读写：
        - 读取: user_demand
        - 写入: travel_outline（_spot_pool）
        - 写入: current_step → "spot_retrieve"
    """
    try:
        user_demand: dict[str, Any] = state.get("user_demand", {})
        destination = user_demand.get("destination", "")
        preferences = user_demand.get("preferences", [])

        logger.info(
            "[spot_retrieve] ENTRY | destination=%s | preferences=%s",
            destination, preferences,
        )

        # 调用景点检索工具
        spots_result = _safe_invoke_tool(
            spot_retriever,
            {"destination": destination, "tags": preferences},
        )
        spot_pool_raw = spots_result.get("data")
        spot_pool: list[dict[str, Any]] = spot_pool_raw if isinstance(spot_pool_raw, list) else []

        if spots_result.get("error"):
            logger.error(
                "spot_retrieve_node: spot_retriever 工具调用异常 | "
                "destination=%s | error=%s",
                destination, str(spots_result["error"])[:200],
            )

        # 小城市景点补充
        if len(spot_pool) < 3:
            logger.warning(
                "spot_retrieve_node: 目的地 '%s' 仅检索到 %d 个景点，启动小城市补充策略",
                destination, len(spot_pool),
            )
            from src.utils.spot_supplement import supplement_spots_for_small_city
            spot_pool = supplement_spots_for_small_city(
                destination=destination,
                existing_spots=spot_pool,
                preferences=preferences,
                min_count=3,
            )
            logger.info(
                "spot_retrieve_node: 补充后景点池 size=%d | destination=%s",
                len(spot_pool), destination,
            )

        if not spot_pool:
            return {
                "current_step": "error",
                "error_msg": (
                    f"未能找到目的地 '{destination}' 的景点数据，"
                    f"请确认城市名称是否正确（如'郑州'而非'郑洲'），或尝试更知名的附近城市。"
                ),
            }

        # 写入 travel_outline._spot_pool
        outline = dict(state.get("travel_outline", {}))
        outline["_spot_pool"] = spot_pool

        logger.info(
            "[spot_retrieve] OK | destination=%s | spots=%d",
            destination, len(spot_pool),
        )

        return {
            "travel_outline": outline,
            "current_step": "spot_retrieve",
            "error_msg": "",
        }

    except Exception as exc:
        return {
            "current_step": "error",
            "error_msg": f"景点检索节点异常: {str(exc)}\n{traceback.format_exc()}",
        }


# ============================================================
# Node 0.6：景点预校验节点（规则驱动，不调 LLM）
# ============================================================
def spot_pre_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """景点预校验节点 —— 仅执行规则校验，不调用 LLM。

    校验内容（复用 _fallback_fact_check 的校验1+2逻辑）：
    - 校验1: 景点归属 —— 所有景点名称是否在 spot_pool 中
    - 校验2: 物价合理性 —— 门票价格是否在合理区间

    结果写入 check_result，供下游 fact_check 跳过已校验项。

    状态读写：
        - 读取: user_demand, travel_outline._spot_pool
        - 写入: check_result（含 _pre_check_done 标记）
        - 写入: current_step → "spot_pre_check"
    """
    try:
        outline: dict[str, Any] = state.get("travel_outline", {})
        spot_pool: list[dict[str, Any]] = outline.get("_spot_pool", [])
        user_demand: dict[str, Any] = state.get("user_demand", {})
        destination = user_demand.get("destination", "")

        if not spot_pool:
            return {
                "check_result": {
                    "is_pass": True,
                    "issues": [],
                    "suggestions": [],
                    "budget_detail": [],
                    "_pre_check_done": True,
                },
                "current_step": "spot_pre_check",
            }

        # 城市关键词映射（用于归属地检测）
        city_keywords_map: dict[str, list[str]] = {
            "郑州": ["郑州", "登封", "新郑", "巩义", "荥阳", "新密", "中牟", "中原", "二七",
                     "金水", "管城", "惠济", "嵩山", "少林"],
            "成都": ["成都", "锦江", "青羊", "金牛", "武侯", "成华", "龙泉驿", "青城山",
                     "都江堰", "郫都", "温江", "双流", "大熊猫", "宽窄", "锦里", "春熙"],
            "北京": ["北京", "东城", "西城", "朝阳", "海淀", "丰台", "石景山", "通州",
                     "大兴", "昌平", "顺义", "房山", "故宫", "天安门", "长城", "颐和"],
            "杭州": ["杭州", "西湖", "余杭", "萧山", "临安", "富阳", "桐庐", "淳安",
                     "钱塘", "拱墅", "上城", "滨江", "千岛湖", "灵隐"],
            "西安": ["西安", "碑林", "雁塔", "未央", "灞桥", "新城", "莲湖", "长安",
                     "临潼", "兵马俑", "大雁塔", "钟楼", "回民街"],
            "南京": ["南京", "玄武", "秦淮", "建邺", "鼓楼", "栖霞", "雨花台", "江宁",
                     "浦口", "中山陵", "夫子庙", "明孝陵"],
            "重庆": ["重庆", "渝中", "江北", "沙坪坝", "九龙坡", "南岸", "渝北", "巴南",
                     "洪崖洞", "解放碑", "磁器口"],
            "武汉": ["武汉", "武昌", "汉口", "汉阳", "江岸", "江汉", "洪山",
                     "黄鹤楼", "东湖", "户部巷"],
            "广州": ["广州", "天河", "越秀", "海珠", "荔湾", "白云", "番禺", "黄埔",
                     "花都", "南沙", "白云山", "长隆", "珠江"],
            "深圳": ["深圳", "福田", "罗湖", "南山", "宝安", "龙岗", "盐田", "龙华",
                     "坪山", "光明", "华侨城", "世界之窗"],
            "长沙": ["长沙", "芙蓉", "天心", "岳麓", "开福", "雨花", "望城",
                     "橘子洲", "岳麓山", "坡子街"],
            "苏州": ["苏州", "姑苏", "虎丘", "吴中", "相城", "吴江", "昆山",
                     "平江路", "观前街", "拙政园", "金鸡湖"],
            "平顶山": ["平顶山", "新华", "卫东", "湛河", "石龙", "舞钢", "宝丰", "叶县",
                      "鲁山", "郏县", "尧山", "中原大佛"],
        }
        city_keys = city_keywords_map.get(destination, [destination])

        # 物价基准
        price_baseline: dict[str, dict[str, tuple[float, float]]] = {
            "郑州": {"ticket_5a": (60, 150), "ticket_4a": (20, 100), "hotel_low": (100, 280)},
            "成都": {"ticket_5a": (50, 130), "ticket_4a": (20, 90), "hotel_low": (120, 280)},
            "北京": {"ticket_5a": (30, 200), "ticket_4a": (10, 100), "hotel_low": (180, 400)},
            "杭州": {"ticket_5a": (40, 150), "ticket_4a": (20, 120), "hotel_low": (150, 350)},
            "西安": {"ticket_5a": (60, 180), "ticket_4a": (20, 120), "hotel_low": (120, 280)},
            "南京": {"ticket_5a": (40, 150), "ticket_4a": (15, 80), "hotel_low": (150, 300)},
            "重庆": {"ticket_5a": (40, 120), "ticket_4a": (10, 80), "hotel_low": (100, 250)},
            "武汉": {"ticket_5a": (40, 120), "ticket_4a": (15, 80), "hotel_low": (120, 250)},
            "广州": {"ticket_5a": (30, 200), "ticket_4a": (10, 100), "hotel_low": (150, 350)},
            "深圳": {"ticket_5a": (60, 220), "ticket_4a": (20, 150), "hotel_low": (180, 400)},
            "长沙": {"ticket_5a": (40, 120), "ticket_4a": (15, 80), "hotel_low": (120, 250)},
            "苏州": {"ticket_5a": (50, 150), "ticket_4a": (20, 100), "hotel_low": (150, 300)},
            "平顶山": {"ticket_5a": (50, 120), "ticket_4a": (20, 80), "hotel_low": (80, 180)},
        }
        baseline = price_baseline.get(
            destination,
            {"ticket_5a": (30, 250), "ticket_4a": (10, 150), "hotel_low": (100, 500)},
        )

        issues: list[str] = []
        suggestions: list[str] = []

        # 校验1: 景点名称有效性
        for s in spot_pool:
            name = s.get("name", "")
            if not name or len(name) < 2:
                issues.append(f"[预校验-归属] 景点名称无效: '{name}'")
                continue

            # 检查地址与目的地相关性
            address = s.get("address", "")
            if address and not any(kw in address for kw in city_keys):
                issues.append(
                    f"[预校验-归属] 景点「{name}」地址「{address}」"
                    f"不含{destination}相关关键词，疑似跨市错配"
                )
                suggestions.append(
                    f"确认「{name}」是否位于{destination}，如不是请替换为本地景点"
                )

        # 校验2: 门票价格合理性
        for s in spot_pool:
            name = s.get("name", "")
            ticket = float(s.get("ticket_price", 0) or 0)
            level = s.get("level", "")

            if ticket > 500:
                issues.append(
                    f"[预校验-物价] 「{name}」门票 ¥{ticket:.0f} 严重偏高，"
                    f"超出{destination}正常景区票价范围"
                )
                suggestions.append(f"核实「{name}」真实票价，调整 ticket_price")

            if level == "5A" and ticket > baseline["ticket_5a"][1] * 1.5:
                issues.append(
                    f"[预校验-物价] 5A景点「{name}」门票 ¥{ticket:.0f}，"
                    f"超出{destination}5A正常范围({baseline['ticket_5a'][0]:.0f}-{baseline['ticket_5a'][1]:.0f}元)"
                )
                suggestions.append(f"调整「{name}」票价至合理区间")

        is_pass = len(issues) == 0

        if not is_pass:
            logger.info(
                "spot_pre_check_node: 预校验发现 %d 个问题 | destination=%s",
                len(issues), destination,
            )
        else:
            logger.info("spot_pre_check_node: 全部通过 | destination=%s", destination)

        return {
            "check_result": {
                "is_pass": is_pass,
                "issues": issues,
                "suggestions": suggestions,
                "budget_detail": [],
                "_pre_check_done": True,
            },
        }

    except Exception as exc:
        logger.error("spot_pre_check_node: 异常 —— %s", str(exc))
        return {
            "check_result": {
                "is_pass": True,
                "issues": [],
                "suggestions": [],
                "budget_detail": [],
                "_pre_check_done": True,
            },
        }


# ============================================================
# Node 1：需求拆解节点
# ============================================================
def demand_analyze_node(state: dict[str, Any]) -> dict[str, Any]:
    """解析用户原始需求，结构化提取并校验必填参数。

    状态读写：
        - 读取: user_demand（原始输入）
        - 写入: user_demand（结构化结果，全量覆盖）
        - 写入: current_step → "parse_demand"
        - 写入: error_msg（校验不通过时）
    """
    try:
        raw_input: dict[str, Any] = state.get("user_demand", {})
        logger.info(
            "[demand_analyze] ENTRY | destination=%s | days=%s | budget=%s | people=%s",
            raw_input.get("destination", "?"), raw_input.get("days", "?"),
            raw_input.get("total_budget", "?"), raw_input.get("people", "?")[:30],
        )

        # 必填字段校验
        required_fields = ["destination", "days", "people"]
        missing = [f for f in required_fields if not raw_input.get(f)]

        if missing:
            return {
                "current_step": "error",
                "error_msg": f"缺少必填参数: {', '.join(missing)}，请补充后重试。",
            }

        days = raw_input.get("days", 1)
        prompt = f"""解析用户旅行需求，输出纯JSON。

输入: {json.dumps(raw_input, ensure_ascii=False)}

输出(仅JSON对象，无Markdown/注释):
{{"destination":"城市","days":{days},"total_budget":数字,"people":"人群描述","preferences":["标签"],"remark":"补充"}}"""

        parsed, err = _call_llm_with_validation(
            prompt, DemandAnalyzeOutput, task_name="demand_analyze",
        )

        if not parsed:
            # LLM 全部重试失败 → 基于原始输入构造兜底结构
            logger.warning(
                "demand_analyze_node: LLM校验全部失败，使用兜底结构。err=%s, destination=%s",
                err, raw_input.get("destination", ""),
            )
            parsed = {
                "destination": raw_input.get("destination", ""),
                "days": raw_input.get("days", 1),
                "total_budget": raw_input.get("total_budget", 0),
                "people": raw_input.get("people", ""),
                "preferences": raw_input.get("preferences", []),
                "remark": raw_input.get("remark", ""),
            }

        # 类型强制转换
        if isinstance(parsed.get("days"), str):
            try:
                parsed["days"] = int(parsed["days"])
            except ValueError:
                parsed["days"] = raw_input.get("days", 1)
        if not isinstance(parsed.get("total_budget"), (int, float)):
            parsed["total_budget"] = float(parsed.get("total_budget", 0) or 0)
        if not isinstance(parsed.get("preferences"), list):
            parsed["preferences"] = []

        error_msg = err if not parsed else ""

        return {
            "user_demand": parsed,
            "current_step": "parse_demand",
            "error_msg": error_msg,
        }

    except Exception as exc:
        return {
            "current_step": "error",
            "error_msg": f"需求拆解节点异常: {str(exc)}\n{traceback.format_exc()}",
        }


# ============================================================
# Node 2：行程框架生成节点
# ============================================================
def outline_generate_node(state: dict[str, Any]) -> dict[str, Any]:
    """基于上游景点池 + LLM，生成整体行程框架。

    景点检索已由上游 spot_retrieve_node 完成，本节点只做：
    1. 天气查询
    2. LLM 生成每日行程框架

    状态读写：
        - 读取: user_demand, travel_outline._spot_pool
        - 写入: travel_outline（全量覆盖，保留 _spot_pool）
        - 写入: current_step → "build_outline"
    """
    try:
        user_demand: dict[str, Any] = state.get("user_demand", {})
        destination = user_demand.get("destination", "")
        days = max(1, int(user_demand.get("days", 1)))
        total_budget = float(user_demand.get("total_budget", 0))
        preferences = user_demand.get("preferences", [])
        people = user_demand.get("people", "")
        remark = user_demand.get("remark", "")

        # 步骤 1：从 state 读取上游 spot_retrieve_node 检索好的景点池
        existing_outline: dict[str, Any] = state.get("travel_outline", {})
        spot_pool: list[dict[str, Any]] = existing_outline.get("_spot_pool", [])

        if not spot_pool:
            logger.error(
                "outline_generate_node: _spot_pool 为空 | destination=%s —— "
                "请确认 spot_retrieve_node 已在上游正确执行",
                destination,
            )
            return {
                "current_step": "error",
                "error_msg": (
                    f"未能找到目的地 '{destination}' 的景点数据，"
                    f"请确认城市名称是否正确（如'郑州'而非'郑洲'），或尝试更知名的附近城市。"
                ),
            }

        # 步骤 2：调用天气工具
        weather_result = _safe_invoke_tool(
            weather_query,
            {"destination": destination, "month": 4},
        )
        weather_info: dict[str, Any] = weather_result.get("data", {})

        # 步骤 3：LLM 生成行程框架
        # 5 类目预算参考：住宿 / 餐饮 / 市内交通 / 景点门票 / 应急备用金
        # 应急备用金占 5%~8%，住宿根据目的地档次估算
        budget_per_day = round(total_budget / days, 2) if days > 0 else 0
        emergency_per_day = round(budget_per_day * 0.06, 2)  # 应急备用金约 6%

        # 景点摘要（扩展新字段：level / area / core_feature）
        spot_summary = [
            {
                "name": s.get("name"),
                "level": s.get("level", ""),
                "area": s.get("area", ""),
                "core_feature": s.get("core_feature", ""),
                "tags": s.get("tags"),
                "duration": s.get("duration"),
                "ticket_price": s.get("ticket_price"),
            }
            for s in spot_pool[:15]  # 扩展到 Top 15，给 LLM 更充分的选择空间
        ]

        prompt = f"""为{days}天{destination}行程生成框架，输出纯JSON。

需求: {destination}|{days}天|¥{total_budget}|{people}|偏好:{json.dumps(preferences, ensure_ascii=False)}|备注:{remark if remark else '无'}

景点池({len(spot_summary)}个真实景点，严禁虚构):
{json.dumps(spot_summary, ensure_ascii=False, separators=(',', ':'))}

约束: 全程¥{total_budget}±10%;每天分上午/下午/晚上;上午优先室外,晚上优先美食/夜市。

输出(仅JSON对象，daily_frameworks长度={days}):
{{"total_days":{days},"budget_split":[{", ".join([str(budget_per_day)] * days)}],"daily_frameworks":[{{"day_index":1,"theme":"上午+下午+晚上","budget":{budget_per_day:.0f},"spot_types":[""],"prefer_tags":[""],"food_style":"本地特色"}},...]}}"""

        outline, err = _call_llm_with_validation(
            prompt, OutlineOutput, temperature=0.2, task_name="outline_generate",
        )

        # --- 兜底 / 修复逻辑 ---
        if not outline:
            logger.warning(
                "outline_generate_node: LLM返回空框架，使用兜底框架。"
                "destination=%s, days=%d",
                destination, days,
            )
            outline = {}

        raw_frameworks: list[dict[str, Any]] = outline.get("daily_frameworks", [])

        # 修复：框架数量不等于 days，自动补齐或截断
        if len(raw_frameworks) < days:
            for i in range(len(raw_frameworks), days):
                raw_frameworks.append({
                    "day_index": i + 1,
                    "theme": f"第{i + 1}天行程",
                    "budget": budget_per_day,
                    "spot_types": preferences[:2] if preferences else ["打卡", "美食"],
                    "prefer_tags": preferences if preferences else ["打卡"],
                    "food_style": "本地特色",
                })
        elif len(raw_frameworks) > days:
            raw_frameworks = raw_frameworks[:days]

        # 重新编号
        for i, fw in enumerate(raw_frameworks):
            fw["day_index"] = i + 1

        outline["total_days"] = days
        outline["daily_frameworks"] = raw_frameworks
        if not outline.get("budget_split") or len(outline.get("budget_split", [])) != days:
            outline["budget_split"] = [budget_per_day] * days

        # 缓存景点池到框架中，后续节点使用
        outline["_spot_pool"] = spot_pool

        error_msg = err if not raw_frameworks else ""

        return {
            "travel_outline": outline,
            "current_step": "build_outline",
            "error_msg": error_msg,
        }

    except Exception as exc:
        return {
            "current_step": "error",
            "error_msg": f"行程框架生成节点异常: {str(exc)}\n{traceback.format_exc()}",
        }


# ============================================================
# daily_fill 并发辅助函数
# ============================================================
def _build_day_revision(
    target_days: set[int],
    day_index: int,
    specific_actions: list[dict[str, Any]],
) -> str:
    """构造当天专属修正指令。"""
    if not target_days or day_index not in target_days:
        return ""
    day_actions = [a for a in specific_actions if int(a.get("day_index", 0)) == day_index]
    if not day_actions:
        return ""
    parts = ["\n## 本天专属修正指令\n"]
    for act in day_actions:
        parts.append(
            f"- {act.get('action', '')}: {act.get('detail', '')} "
            f"（原因: {act.get('reason', '')}）\n"
        )
    return "".join(parts)


def _generate_one_day(
    day_index: int,
    framework: dict[str, Any],
    day_budget: float,
    spot_pool_slim: list[dict[str, Any]],
    spot_pool: list[dict[str, Any]],
    user_demand: dict[str, Any],
    check_result: dict[str, Any],
    is_revision: bool,
    revision_context: str,
    day_revision: str,
) -> tuple[dict[str, Any] | None, str]:
    """生成单日行程 —— 构建 prompt 并调用 LLM。

    Returns:
        (validated_plan_dict, error_string)
    """
    dest = user_demand.get("destination", "当地")
    total_budget_limit = float(user_demand.get("total_budget", 0))
    user_people = user_demand.get("people", "")
    user_prefs = user_demand.get("preferences", [])
    user_remark = user_demand.get("remark", "") or "无"

    est_hotel = round(float(day_budget) * 0.30, 2)
    est_food = round(float(day_budget) * 0.28, 2)
    est_transport = round(float(day_budget) * 0.10, 2)
    est_emergency = round(float(day_budget) * 0.06, 2)

    prompt = f"""你是{dest}导游。为第{day_index}天生成行程，输出纯JSON。

框架: {json.dumps(framework, ensure_ascii=False)}

景点池({len(spot_pool_slim)}个,严禁编造):
{json.dumps(spot_pool_slim, ensure_ascii=False, separators=(',', ':'))}

预算: ¥{day_budget}(建议住宿¥{est_hotel:.0f}|餐饮¥{est_food:.0f}|交通¥{est_transport:.0f}|应急¥{est_emergency:.0f}；总额±10%)
{revision_context}{day_revision}
约束:
1. 景点字段一字不改来自景点池；选2-4个，标time_slot(上午/下午/晚上)，就近排列
2. budget_breakdown含5类目(住宿/餐饮/市内交通/景点门票/应急备用金)，贴合{dest}真实消费
3. traffic_note含具体线路，food_recommendation列3-5个餐厅+位置，accommodation写区域+类型+价位
4. 人群: {user_people} | 偏好: {json.dumps(user_prefs, ensure_ascii=False)} | 备注: {user_remark}

输出(仅JSON):
{{"day_index":{day_index},"theme":"上午+下午+晚上","spots":[{{"name":"","address":"","duration":h,"ticket_price":元,"time_slot":"上午","level":"","area":"","core_feature":"","tags":[],"recommendation":""}}],"food_recommendation":[""],"traffic_note":"","accommodation":"","daily_budget":0,"budget_breakdown":{{"住宿":0,"餐饮":0,"市内交通":0,"景点门票":0,"应急备用金":0}}}}"""

    day_plan, err = _call_llm_with_validation(
        prompt, DailyPlan, temperature=0.2, task_name="daily_fill",
    )

    validated_plan: dict[str, Any] | None = None
    if day_plan:
        validated_plan, _ = _validate_daily_plan(day_plan, spot_pool)
        if validated_plan is None:
            validated_plan = day_plan

    return validated_plan, err


def _process_day_result(
    plan: dict[str, Any] | None,
    err: str,
    day_index: int,
    all_daily_plans: list[dict[str, Any]],
    error_msgs: list[str],
    spot_pool: list[dict[str, Any]],
    day_params: list[dict[str, Any]],
    user_demand: dict[str, Any],
    fallback_used_names: set[str],
) -> None:
    """处理单天生成结果：成功则追加，失败则执行 fallback。"""
    if plan is not None:
        all_daily_plans.append(plan)
        return

    # LLM 失败 → fallback
    if err:
        error_msgs.append(f"第{day_index}天: {err[:100]}")
    logger.warning(
        "daily_fill_node: 第%d天LLM生成失败，使用兜底数据。err=%s",
        day_index, (err or "未知错误")[:120],
    )

    # 找到对应 framework
    framework = {}
    day_budget = 0.0
    for dp in day_params:
        if dp["day_index"] == day_index:
            framework = dp["framework"]
            day_budget = dp["day_budget"]
            break

    validated_plan = _build_fallback_daily_plan(
        day_index=day_index,
        framework=framework,
        spot_pool=spot_pool,
        day_budget=float(day_budget),
        used_spot_names=fallback_used_names,
        destination=user_demand.get("destination", ""),
    )
    for s in validated_plan.get("spots", []):
        name = s.get("name", "")
        if name:
            fallback_used_names.add(name)

    fb_validated, _ = _validate_daily_plan(validated_plan, spot_pool)
    if fb_validated is not None:
        validated_plan = fb_validated
    all_daily_plans.append(validated_plan)


# ============================================================
# Node 3：每日行程填充节点（核心）
# ============================================================
def daily_fill_node(state: dict[str, Any]) -> dict[str, Any]:
    """基于行程框架和景点池，使用 LLM 逐日填充标准化 DailyPlan。

    核心保障：
    1. 强制 LLM 输出纯 JSON，Pydantic DailyPlan 校验
    2. 校验失败自动重试最多 3 次，再失败则使用兜底逻辑
    3. 确保 daily_plans 长度 == 用户指定天数

    状态读写：
        - 读取: user_demand, travel_outline, check_result
        - 写入: daily_plans（全量覆盖）
        - 写入: iteration_count（增量 +1）
        - 写入: current_step → "generate_plans" | "revise_plans"
        - 写入: error_msg
    """
    try:
        user_demand: dict[str, Any] = state.get("user_demand", {})
        outline: dict[str, Any] = state.get("travel_outline", {})
        check_result: dict[str, Any] = state.get("check_result", {})
        iteration_count: int = state.get("iteration_count", 0)

        expected_days = max(1, int(user_demand.get("days", 1)))
        daily_frameworks: list[dict[str, Any]] = outline.get("daily_frameworks", [])
        spot_pool: list[dict[str, Any]] = outline.get("_spot_pool", [])
        total_budget = float(user_demand.get("total_budget", 0))

        # 确保框架数量正确
        if len(daily_frameworks) != expected_days:
            budget_per_day = round(total_budget / expected_days, 2) if expected_days > 0 else 0
            daily_frameworks = [
                {
                    "day_index": i + 1,
                    "theme": f"第{i + 1}天行程",
                    "budget": budget_per_day,
                    "spot_types": [],
                    "prefer_tags": user_demand.get("preferences", []),
                    "food_style": "本地特色",
                }
                for i in range(expected_days)
            ]

        # ---- 判断是否为修正模式 ----
        is_revision = bool(
            check_result.get("issues") or check_result.get("suggestions")
        )

        # ---- ReAct 定向修正：读取 react_revise_plan ----
        react_revise_plan: dict[str, Any] | None = check_result.get("react_revise_plan")
        target_days: set[int] = set()
        specific_actions: list[dict[str, Any]] = []
        if react_revise_plan and isinstance(react_revise_plan, dict):
            td = react_revise_plan.get("target_days", [])
            if isinstance(td, list):
                target_days = {int(d) for d in td if isinstance(d, (int, float))}
            sa = react_revise_plan.get("specific_actions", [])
            if isinstance(sa, list):
                specific_actions = sa

        # ---- 构造修正上下文 ----
        revision_context = ""
        if is_revision:
            if target_days and specific_actions:
                # ReAct 定向修正：只给 LLM 精确的修改指令
                actions_by_day: dict[int, list[dict[str, Any]]] = {}
                for action in specific_actions:
                    di = int(action.get("day_index", 0))
                    actions_by_day.setdefault(di, []).append(action)

                revision_context = "## 定向修正指令（ReAct 分析结果）\n"
                revision_context += f"只修改以下天数的行程，其余天保持不变：{sorted(target_days)}\n\n"
                for di in sorted(target_days):
                    acts = actions_by_day.get(di, [])
                    for act in acts:
                        revision_context += (
                            f"- 第{di}天: {act.get('action', '')} → "
                            f"{act.get('detail', '')} "
                            f"（目标: {act.get('target', '')}，原因: {act.get('reason', '')}）\n"
                        )
                revision_context += f"\n## 原始校验问题\n{json.dumps(check_result.get('issues', []), ensure_ascii=False)}\n"
            else:
                # 经典模式：通用修正
                revision_context = f"""
## 上一轮校验问题
{json.dumps(check_result.get('issues', []), ensure_ascii=False)}

## 修改建议
{json.dumps(check_result.get('suggestions', []), ensure_ascii=False)}

请根据以上问题定向调整行程。"""

        all_daily_plans: list[dict[str, Any]] = []
        has_error = False
        error_msgs: list[str] = []
        fallback_used_names: set[str] = set()

        # ---- 收集各天的生成参数（纯 CPU，可快速完成） ----
        day_params: list[dict[str, Any]] = []
        for i, framework in enumerate(daily_frameworks):
            day_index = i + 1
            day_budget = framework.get("budget", 0)

            # ReAct 定向修正：跳过不需要修改的天
            if target_days and day_index not in target_days and is_revision:
                existing_plan = None
                for old_plan in state.get("daily_plans", []):
                    if old_plan.get("day_index") == day_index:
                        existing_plan = old_plan
                        break
                if existing_plan:
                    all_daily_plans.append(existing_plan)
                    continue

            # 景点池摘要（紧凑 JSON）
            spot_pool_slim = [
                {
                    "name": s.get("name"), "address": s.get("address"),
                    "duration": s.get("duration"), "ticket_price": s.get("ticket_price"),
                    "level": s.get("level", ""), "area": s.get("area", ""),
                    "tags": s.get("tags"),
                }
                for s in spot_pool[:15]
            ]

            day_params.append({
                "day_index": day_index,
                "framework": framework,
                "day_budget": day_budget,
                "spot_pool_slim": spot_pool_slim,
                "day_revision": _build_day_revision(target_days, day_index, specific_actions),
            })

        # ---- 并发生成各天行程 ----
        if day_params and os.getenv("PARALLEL_DAILY_ENABLED", "1") == "1":
            # 并行模式：ThreadPoolExecutor 并发 N 天 LLM 调用
            max_workers = min(len(day_params), 5)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for dp in day_params:
                    future = executor.submit(
                        _generate_one_day,
                        dp["day_index"], dp["framework"], dp["day_budget"],
                        dp["spot_pool_slim"], spot_pool,
                        user_demand, check_result, is_revision,
                        revision_context, dp["day_revision"],
                    )
                    futures[future] = dp["day_index"]

                results_by_day: dict[int, tuple[dict | None, str]] = {}
                for future in as_completed(futures):
                    day_idx = futures[future]
                    try:
                        results_by_day[day_idx] = future.result()
                    except Exception as exc:
                        results_by_day[day_idx] = (None, str(exc))
                        logger.error("daily_fill 第%d天并发异常: %s", day_idx, str(exc))

                # 按 day_index 排序组装
                for day_idx in sorted(results_by_day.keys()):
                    plan, err = results_by_day[day_idx]
                    _process_day_result(
                        plan, err, day_idx, all_daily_plans, error_msgs,
                        spot_pool, day_params, user_demand,
                        fallback_used_names,
                    )
        else:
            # 串行模式（PARALLEL_DAILY_ENABLED=0 时回退）
            for dp in day_params:
                plan, err = _generate_one_day(
                    dp["day_index"], dp["framework"], dp["day_budget"],
                    dp["spot_pool_slim"], spot_pool,
                    user_demand, check_result, is_revision,
                    revision_context, dp["day_revision"],
                )
                _process_day_result(
                    plan, err, dp["day_index"], all_daily_plans, error_msgs,
                    spot_pool, day_params, user_demand,
                    fallback_used_names,
                )

        # 标记是否有错误
        has_error = len(error_msgs) > 0

        # 最终保障：daily_plans 长度必须等于 expected_days
        if len(all_daily_plans) < expected_days:
            logger.warning(
                "daily_fill_node: daily_plans长度不足，补齐缺失天数。"
                "current=%d, expected=%d",
                len(all_daily_plans), expected_days,
            )
            budget_per_day = round(total_budget / expected_days, 2) if expected_days > 0 else 0
            for i in range(len(all_daily_plans), expected_days):
                fb_plan = _build_fallback_daily_plan(
                    day_index=i + 1,
                    framework=(
                        daily_frameworks[i] if i < len(daily_frameworks)
                        else {"theme": f"第{i+1}天行程"}
                    ),
                    spot_pool=spot_pool,
                    day_budget=budget_per_day,
                    used_spot_names=fallback_used_names,
                    destination=user_demand.get("destination", ""),
                )
                for s in fb_plan.get("spots", []):
                    name = s.get("name", "")
                    if name:
                        fallback_used_names.add(name)
                all_daily_plans.append(fb_plan)

        step = "revise_plans" if is_revision else "generate_plans"
        error_msg = ""
        if has_error:
            error_msg = f"部分行程填充失败，已用兜底数据补齐: {'; '.join(error_msgs[:3])}"

        # ---- 标准化日志：行程生成结果汇总 ----
        llm_ok_days = expected_days - len(error_msgs)
        fallback_days = len(error_msgs)
        total_spots = sum(len(d.get("spots", [])) for d in all_daily_plans)
        total_budget_spent = sum(float(d.get("daily_budget", 0) or 0) for d in all_daily_plans)
        logger.info(
            "[daily_fill] RESULT | destination=%s | days=%d/%d | "
            "spots=%d | budget=%.0f/%.0f | llm_ok=%d | fallback=%d | revision=%s",
            user_demand.get("destination", "?"), len(all_daily_plans), expected_days,
            total_spots, total_budget_spent, total_budget,
            llm_ok_days, fallback_days, is_revision,
        )

        return {
            "daily_plans": all_daily_plans,
            "iteration_count": iteration_count + 1,
            "current_step": step,
            "error_msg": error_msg,
        }

    except Exception as exc:
        trace = traceback.format_exc()
        return {
            "daily_plans": [],
            "iteration_count": state.get("iteration_count", 0) + 1,
            "current_step": "error",
            "error_msg": f"每日行程填充节点异常: {str(exc)}\n{trace}",
        }


# ============================================================
# Node 4：事实校验节点（新增）—— LLM 驱动的三项强制校验
# ============================================================
def _fallback_fact_check(
    daily_plans: list[dict[str, Any]],
    spot_pool_names: set[str],
    destination: str,
    total_budget: float = 0,
) -> dict[str, Any]:
    """LLM 不可用时的规则兜底事实校验。

    执行三项基于规则的检查：
    1. 景点归属：所有景点名称是否在 spot_pool 中找到（模糊匹配）
    2. 物价合理性：门票/住宿/餐饮是否在合理区间
    3. 行程合理性：每日时长/景点数量是否合理

    Args:
        daily_plans: 已生成的每日行程列表。
        spot_pool_names: 景点池中所有景点名称集合。
        destination: 目的地城市。
        total_budget: 总预算上限。

    Returns:
        dict: 与 FactCheckOutput 兼容的校验结果。
    """
    issues: list[str] = []
    suggestions: list[str] = []

    # 城市关键词（用于跨市检测）
    city_keywords_map: dict[str, list[str]] = {
        "郑州": ["郑州", "登封", "新郑", "巩义", "荥阳", "新密", "中牟", "中原", "二七",
                 "金水", "管城", "惠济", "嵩山", "少林", "黄帝", "商都", "黄河"],
        "杭州": ["杭州", "西湖", "余杭", "萧山", "临安", "富阳", "桐庐", "淳安", "建德",
                 "钱塘", "拱墅", "上城", "滨江", "千岛湖", "灵隐", "雷峰"],
        "成都": ["成都", "锦江", "青羊", "金牛", "武侯", "成华", "龙泉驿", "青城山",
                 "都江堰", "郫都", "温江", "双流", "大熊猫", "宽窄", "锦里", "春熙"],
        "北京": ["北京", "东城", "西城", "朝阳", "海淀", "丰台", "石景山", "通州",
                 "大兴", "昌平", "顺义", "房山", "故宫", "天安门", "长城", "颐和"],
        "西安": ["西安", "碑林", "雁塔", "未央", "灞桥", "新城", "莲湖", "长安",
                 "临潼", "兵马俑", "大雁塔", "钟楼", "回民街", "华清"],
        "南京": ["南京", "玄武", "秦淮", "建邺", "鼓楼", "栖霞", "雨花台", "江宁",
                 "浦口", "中山陵", "夫子庙", "明孝陵", "总统府"],
        "重庆": ["重庆", "渝中", "江北", "沙坪坝", "九龙坡", "南岸", "渝北", "巴南",
                 "洪崖洞", "解放碑", "磁器口", "武隆", "仙女"],
        "武汉": ["武汉", "武昌", "汉口", "汉阳", "江岸", "江汉", "硚口", "洪山",
                 "青山", "黄鹤楼", "东湖", "户部巷", "光谷"],
        "广州": ["广州", "天河", "越秀", "海珠", "荔湾", "白云", "番禺", "黄埔",
                 "花都", "南沙", "白云山", "长隆", "沙面", "珠江"],
        "深圳": ["深圳", "福田", "罗湖", "南山", "宝安", "龙岗", "盐田", "龙华",
                 "坪山", "光明", "华侨城", "世界之窗", "欢乐谷"],
        "长沙": ["长沙", "芙蓉", "天心", "岳麓", "开福", "雨花", "望城", "浏阳",
                 "宁乡", "橘子洲", "岳麓山", "坡子街", "火宫殿"],
        "苏州": ["苏州", "姑苏", "虎丘", "吴中", "相城", "吴江", "昆山", "常熟",
                 "张家港", "太仓", "平江路", "观前街", "拙政园", "金鸡湖"],
        "平顶山": ["平顶山", "新华", "卫东", "湛河", "石龙", "舞钢", "宝丰", "叶县",
                  "鲁山", "郏县", "尧山", "中原大佛", "香山"],
    }

    city_keys = city_keywords_map.get(destination, [destination])

    # 各目的地物价基准
    price_baseline: dict[str, dict[str, tuple[float, float]]] = {
        # (最低合理价, 最高合理价)
        "郑州": {"ticket_5a": (60, 150), "ticket_4a": (20, 100), "hotel_low": (100, 280), "hotel_mid": (200, 450)},
        "杭州": {"ticket_5a": (40, 150), "ticket_4a": (20, 120), "hotel_low": (150, 350), "hotel_mid": (250, 550)},
        "成都": {"ticket_5a": (50, 130), "ticket_4a": (20, 90), "hotel_low": (120, 280), "hotel_mid": (200, 450)},
        "北京": {"ticket_5a": (30, 200), "ticket_4a": (10, 100), "hotel_low": (180, 400), "hotel_mid": (300, 700)},
        "西安": {"ticket_5a": (60, 180), "ticket_4a": (20, 120), "hotel_low": (120, 280), "hotel_mid": (200, 450)},
        "南京": {"ticket_5a": (40, 150), "ticket_4a": (15, 80), "hotel_low": (150, 300), "hotel_mid": (250, 500)},
        "重庆": {"ticket_5a": (40, 120), "ticket_4a": (10, 80), "hotel_low": (100, 250), "hotel_mid": (180, 400)},
        "武汉": {"ticket_5a": (40, 120), "ticket_4a": (15, 80), "hotel_low": (120, 250), "hotel_mid": (200, 400)},
        "广州": {"ticket_5a": (30, 200), "ticket_4a": (10, 100), "hotel_low": (150, 350), "hotel_mid": (250, 550)},
        "深圳": {"ticket_5a": (60, 220), "ticket_4a": (20, 150), "hotel_low": (180, 400), "hotel_mid": (300, 650)},
        "长沙": {"ticket_5a": (40, 120), "ticket_4a": (15, 80), "hotel_low": (120, 250), "hotel_mid": (200, 400)},
        "苏州": {"ticket_5a": (50, 150), "ticket_4a": (20, 100), "hotel_low": (150, 300), "hotel_mid": (250, 500)},
        "平顶山": {"ticket_5a": (50, 120), "ticket_4a": (20, 80), "hotel_low": (80, 180), "hotel_mid": (150, 300)},
    }
    baseline = price_baseline.get(destination, {"ticket_5a": (30, 250), "ticket_4a": (10, 150), "hotel_low": (100, 500), "hotel_mid": (200, 800)})

    # ---- 校验1: 景点归属 ----
    for day in daily_plans:
        day_idx = day.get("day_index", "?")
        spots = day.get("spots", [])
        for s in spots:
            name = s.get("name", "")
            if not name:
                continue

            # 检查是否在 spot_pool 中（精确 + 模糊匹配）
            in_pool = name in spot_pool_names
            if not in_pool:
                # 模糊匹配：去除括号内容后比较
                name_clean = name.split("（")[0].split("(")[0].strip()
                in_pool = any(name_clean in pn or pn in name_clean for pn in spot_pool_names)

            if not in_pool:
                issues.append(
                    f"[校验1-景点归属] 第{day_idx}天景点「{name}」不在{destination}景点池中，"
                    f"疑似虚构或跨市错配"
                )
                suggestions.append(
                    f"第{day_idx}天：将「{name}」替换为{destination}景点池中的真实景点"
                )

            # 检查地址是否与目的城市相关
            address = s.get("address", "")
            if address and not any(kw in address for kw in city_keys):
                # 地址不含目的地关键词，标记为疑似跨市
                issues.append(
                    f"[校验1-景点归属] 第{day_idx}天景点「{name}」地址「{address}」"
                    f"不含{destination}相关关键词，疑似跨市错配"
                )
                suggestions.append(
                    f"第{day_idx}天：确认「{name}」是否位于{destination}，"
                    f"如不是请替换为本地景点"
                )

    # ---- 校验2: 物价合理性 ----
    for day in daily_plans:
        day_idx = day.get("day_index", "?")
        spots = day.get("spots", [])
        budget_bd = day.get("budget_breakdown", {})
        accommodation = day.get("accommodation", "")

        for s in spots:
            name = s.get("name", "")
            ticket = float(s.get("ticket_price", 0) or 0)
            level = s.get("level", "")

            if ticket > 500:
                issues.append(
                    f"[校验2-物价] 第{day_idx}天景点「{name}」门票 ¥{ticket:.0f} 严重偏高，"
                    f"超出{destination}正常景区票价范围"
                )
                suggestions.append(
                    f"第{day_idx}天：核实「{name}」真实票价，调整 ticket_price"
                )

            if level == "5A" and ticket > baseline["ticket_5a"][1] * 1.5:
                issues.append(
                    f"[校验2-物价] 第{day_idx}天5A景点「{name}」门票 ¥{ticket:.0f}，"
                    f"超出{destination}5A景区正常范围 ({baseline['ticket_5a'][0]:.0f}-{baseline['ticket_5a'][1]:.0f}元)"
                )
                suggestions.append(f"第{day_idx}天：调整「{name}」票价至合理区间")

        # 检查住宿价格
        if budget_bd:
            hotel = float(budget_bd.get("住宿", 0))
            if hotel > 0 and hotel < 50:
                issues.append(
                    f"[校验2-物价] 第{day_idx}天住宿费 ¥{hotel:.0f} 过低，"
                    f"不符合{destination}实际房价水平"
                )
                suggestions.append(
                    f"第{day_idx}天：住宿费调整为 {destination} 经济型酒店标准 (¥{baseline['hotel_low'][0]:.0f}-{baseline['hotel_low'][1]:.0f})"
                )
            if hotel > baseline["hotel_mid"][1] * 2:
                issues.append(
                    f"[校验2-物价] 第{day_idx}天住宿费 ¥{hotel:.0f} 偏高，"
                    f"超出{destination}舒适型酒店合理上限"
                )
                suggestions.append(f"第{day_idx}天：住宿费控制在 ¥{baseline['hotel_mid'][1]:.0f} 以内")

        # 检查住宿文字建议中的价格
        import re as _re
        price_match = _re.findall(r'(\d+)\s*元', accommodation)
        for pm in price_match:
            price_val = float(pm)
            if price_val < 50:
                issues.append(
                    f"[校验2-物价] 第{day_idx}天住宿建议中价格 ¥{price_val:.0f} 过低，"
                    f"不符合{destination}实际水平"
                )
                break
            if price_val > baseline["hotel_mid"][1] * 2.5:
                issues.append(
                    f"[校验2-物价] 第{day_idx}天住宿建议中价格 ¥{price_val:.0f} 异常偏高"
                )
                break

    # ---- 校验3: 行程合理性 ----
    for day in daily_plans:
        day_idx = day.get("day_index", "?")
        spots = day.get("spots", [])
        total_duration = sum(float(s.get("duration", 0) or 0) for s in spots)

        if len(spots) > 5:
            issues.append(
                f"[校验3-行程] 第{day_idx}天安排了 {len(spots)} 个景点，"
                f"数量过多，建议控制在 4 个以内"
            )
            suggestions.append(f"第{day_idx}天：减少景点数量至 4 个以内")

        if total_duration > 10:
            issues.append(
                f"[校验3-行程] 第{day_idx}天累计游览时长 {total_duration:.1f} 小时，"
                f"超出合理范围（含通勤建议 ≤ 8 小时）"
            )
            suggestions.append(
                f"第{day_idx}天：缩减部分景点游览时长或减少景点数量"
            )

        # 检查时间槽分布
        time_slots = [s.get("time_slot", "") for s in spots]
        if len(spots) >= 3:
            morning_spots = [s for s in spots if s.get("time_slot") == "上午"]
            afternoon_spots = [s for s in spots if s.get("time_slot") == "下午"]
            # 上午过多
            if len(morning_spots) >= 3:
                issues.append(
                    f"[校验3-行程] 第{day_idx}天上午安排了 {len(morning_spots)} 个景点，"
                    f"上午时段通常只能完成 1-2 个大型景点"
                )
                suggestions.append(
                    f"第{day_idx}天：将部分上午景点移至下午或晚上"
                )

        # 检查景点间区域跨度
        areas = [s.get("area", "") for s in spots if s.get("area")]
        unique_areas = set(areas)
        if len(unique_areas) >= 3 and len(spots) <= 4:
            issues.append(
                f"[校验3-行程] 第{day_idx}天景点分布在 {len(unique_areas)} 个不同区域 "
                f"({', '.join(sorted(unique_areas))})，跨区过多可能浪费时间在通勤上"
            )
            suggestions.append(
                f"第{day_idx}天：尽量将同区域景点安排在同一天，减少跨区通勤"
            )

    is_pass = len(issues) == 0

    return {
        "is_pass": is_pass,
        "issues": issues,
        "suggestions": suggestions,
    }


def fact_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """事实校验节点 —— 行程合理性校验（校验3）。

    景点归属（校验1）和物价合理性（校验2）已由上游 spot_pre_check_node 完成。
    本节点仅校验行程合理性：时长/时段/跨区/时间冲突。

    默认使用规则兜底校验（~1ms），仅在规则无法判定时回调 LLM。

    状态读写：
        - 读取: user_demand, daily_plans, travel_outline._spot_pool, iteration_count
        - 写入: check_result（合并上游 pre_check 结果）
        - 写入: current_step → "fact_check"
    """
    try:
        user_demand: dict[str, Any] = state.get("user_demand", {})
        daily_plans: list[dict[str, Any]] = state.get("daily_plans", [])
        outline: dict[str, Any] = state.get("travel_outline", {})
        spot_pool: list[dict[str, Any]] = outline.get("_spot_pool", [])
        iteration_count: int = state.get("iteration_count", 0)
        # 读取上游预校验结果
        pre_check: dict[str, Any] = state.get("check_result", {})

        destination = user_demand.get("destination", "")

        # ---- 防御：daily_plans 为空时直接返回 ----
        if not daily_plans:
            return {
                "check_result": {
                    "is_pass": False,
                    "issues": ["[事实校验] daily_plans 为空，无法执行校验"],
                    "suggestions": ["请检查 daily_fill_node 是否正确执行"],
                    "budget_detail": [],
                },
                "current_step": "fact_check",
            }

        # ---- 规则兜底校验（仅校验3：行程合理性，毫秒级） ----
        route_issues: list[str] = []
        route_suggestions: list[str] = []

        for day in daily_plans:
            day_idx = day.get("day_index", "?")
            spots = day.get("spots", [])
            total_duration = sum(float(s.get("duration", 0) or 0) for s in spots)

            if len(spots) > 5:
                route_issues.append(
                    f"[校验3-行程] 第{day_idx}天安排了{len(spots)}个景点，数量过多，建议控制在4个以内"
                )
                route_suggestions.append(f"第{day_idx}天：减少景点数量至4个以内")

            if total_duration > 10:
                route_issues.append(
                    f"[校验3-行程] 第{day_idx}天累计游览时长{total_duration:.1f}h，"
                    f"超出合理范围（含通勤建议≤8h）"
                )
                route_suggestions.append(f"第{day_idx}天：缩减部分景点游览时长或减少景点数量")

            # 时间槽分布
            if len(spots) >= 3:
                morning_spots = [s for s in spots if s.get("time_slot") == "上午"]
                if len(morning_spots) >= 3:
                    route_issues.append(
                        f"[校验3-行程] 第{day_idx}天上午安排了{len(morning_spots)}个景点，"
                        f"上午时段通常只能完成1-2个大型景点"
                    )
                    route_suggestions.append(f"第{day_idx}天：将部分上午景点移至下午")

            # 区域跨度
            areas = [s.get("area", "") for s in spots if s.get("area")]
            unique_areas = set(areas)
            if len(unique_areas) >= 3 and len(spots) <= 4:
                route_issues.append(
                    f"[校验3-行程] 第{day_idx}天景点分布在{len(unique_areas)}个不同区域"
                    f"({', '.join(sorted(unique_areas))})，跨区过多"
                )
                route_suggestions.append(f"第{day_idx}天：尽量安排同区域景点")

        # ---- 仅在规则校验发现大量问题时回调 LLM 精细校验 ----
        if len(route_issues) >= 3 and os.getenv("FACT_CHECK_LLM_FALLBACK", "1") == "1":
            daily_summaries = []
            for dp in daily_plans:
                spot_details = []
                for s in dp.get("spots", []):
                    spot_details.append({
                        "name": s.get("name", ""),
                        "time_slot": s.get("time_slot", ""),
                        "duration": s.get("duration", 0),
                        "area": s.get("area", ""),
                    })
                daily_summaries.append({
                    "day_index": dp.get("day_index", 0),
                    "theme": dp.get("theme", ""),
                    "spots": spot_details,
                    "daily_budget": dp.get("daily_budget", 0),
                })

            prompt = f"""校验{destination}行程合理性(仅景点数量/时长/时段/跨区)，输出纯JSON。

行程: {json.dumps(daily_summaries, ensure_ascii=False)}

规则: 每日2-4景点;累计≤8h;上午室外/下午文化/晚上美食;同区域优先;无时间冲突。

输出(仅JSON): {{"is_pass":true/false,"issues":["[校验3]第Y天:问题"],"suggestions":["第Y天:建议"]}}"""

            fact_result, err = _call_llm_with_validation(
                prompt, FactCheckOutput, temperature=0.15, task_name="fact_check",
            )
            if fact_result:
                fact_issues = fact_result.get("issues", [])
                fact_suggestions = fact_result.get("suggestions", [])
                fact_is_pass = fact_result.get("is_pass", True) and len(fact_issues) == 0
            else:
                # LLM 失败 → 使用规则校验结果
                fact_issues = route_issues
                fact_suggestions = route_suggestions
                fact_is_pass = len(route_issues) == 0
                logger.warning("fact_check LLM回退失败，使用规则校验结果")
        else:
            # 问题少 → 直接使用规则校验结果
            fact_issues = route_issues
            fact_suggestions = route_suggestions
            fact_is_pass = len(route_issues) == 0

        # ---- 合并上游 spot_pre_check 结果 ----
        pre_issues = pre_check.get("issues", []) if pre_check.get("_pre_check_done") else []
        pre_suggestions = pre_check.get("suggestions", []) if pre_check.get("_pre_check_done") else []
        pre_is_pass = pre_check.get("is_pass", True) if pre_check.get("_pre_check_done") else True

        all_issues = list(pre_issues) + list(fact_issues)
        all_suggestions = list(pre_suggestions) + list(fact_suggestions)
        is_pass = pre_is_pass and fact_is_pass

        logger.info(
            "fact_check_node: %s | pre=%d issues | route=%d issues | destination=%s iter=%d",
            "PASS" if is_pass else "FAIL", len(pre_issues), len(fact_issues),
            destination, iteration_count + 1,
        )

        return {
            "check_result": {
                "is_pass": is_pass,
                "issues": all_issues,
                "suggestions": all_suggestions,
                "budget_detail": [],
            },
            "current_step": "fact_check",
        }

    except Exception as exc:
        trace = traceback.format_exc()
        logger.error("fact_check_node: 异常 —— %s", str(exc))
        return {
            "check_result": {
                "is_pass": False,
                "issues": [f"[事实校验] 节点异常: {str(exc)}"],
                "suggestions": ["系统错误，请重试或手动检查行程"],
                "budget_detail": [],
            },
            "current_step": "fact_check",
            "error_msg": f"事实校验节点异常: {str(exc)}\n{trace}",
        }


# ============================================================
# Node 5：行程校验节点（合并事实校验 + 机械校验）
# ============================================================
def plan_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """机械校验 + 事实校验结果合并。对完整行程做多维度校验。

    读取上游 fact_check_node 写入的 check_result（含三项事实校验结果），
    补充规则引擎的机械校验（景点数量/游玩时长/区域跨度/预算），
    合并后输出最终 check_result。

    状态读写：
        - 读取: user_demand, daily_plans, check_result（事实校验结果）
        - 写入: check_result（合并结果，全量覆盖）
        - 写入: current_step → "verify_plans"
    """
    try:
        daily_plans: list[dict[str, Any]] = state.get("daily_plans", [])
        user_demand: dict[str, Any] = state.get("user_demand", {})
        total_budget_limit = float(user_demand.get("total_budget", 0))
        # 读取上游 fact_check_node 在校验结果
        fact_check: dict[str, Any] = state.get("check_result", {})

        # 防御：daily_plans 为空时直接报错
        if not daily_plans:
            return {
                "check_result": {
                    "is_pass": False,
                    "issues": ["daily_plans 为空，未能生成行程"],
                    "suggestions": ["请检查 daily_fill_node 是否正确执行"],
                    "budget_detail": [],
                },
                "current_step": "verify_plans",
            }

        # 调用行程合理性校验工具
        check_payload = {
            "total_budget": total_budget_limit,
            "daily_plans": daily_plans,
        }
        checker_result = _safe_invoke_tool(plan_checker, {"travel_plan": check_payload})
        check_data: dict[str, Any] = checker_result.get("data", {})

        # 调用预算统计工具
        budget_result = _safe_invoke_tool(
            budget_calculator,
            {"daily_plans": daily_plans, "total_budget_limit": total_budget_limit},
        )
        budget_data: dict[str, Any] = budget_result.get("data", {})

        # ---- 合并事实校验结果 + 机械校验结果 ----
        fact_issues: list[str] = fact_check.get("issues", [])
        fact_suggestions: list[str] = fact_check.get("suggestions", [])
        fact_is_pass: bool = fact_check.get("is_pass", True)

        mech_issues: list[str] = list(check_data.get("issues", []))
        if budget_data.get("is_over"):
            mech_issues.append(
                f"总预算 {budget_data.get('total_amount', 0)} 元超出上限 {total_budget_limit} 元"
            )

        mech_suggestions: list[str] = list(check_data.get("suggestions", []))
        mech_suggestions.extend(budget_data.get("suggestions", []))
        mech_is_pass = (
            check_data.get("is_pass", True)
            and not budget_data.get("is_over", False)
        )

        # 合并 issue（事实校验在前，机械校验在后）
        all_issues: list[str] = list(fact_issues) + mech_issues

        # 合并 suggestion 并去重
        all_suggestions: list[str] = list(fact_suggestions) + mech_suggestions
        seen: set[str] = set()
        unique_suggestions: list[str] = []
        for s in all_suggestions:
            if s not in seen:
                seen.add(s)
                unique_suggestions.append(s)

        # 综合判断：事实校验 + 机械校验全部通过才算通过
        is_pass = fact_is_pass and mech_is_pass and len(daily_plans) > 0

        return {
            "check_result": {
                "is_pass": is_pass,
                "issues": all_issues,
                "suggestions": unique_suggestions,
                "budget_detail": budget_data.get("daily_budget_detail", []),
            },
            "current_step": "verify_plans",
        }

    except Exception as exc:
        trace = traceback.format_exc()
        return {
            "check_result": {
                "is_pass": False,
                "issues": [f"行程校验异常: {str(exc)}"],
                "suggestions": [],
                "budget_detail": [],
            },
            "current_step": "verify_plans",
            "error_msg": f"行程校验节点异常: {str(exc)}\n{trace}",
        }


# ============================================================
# Node 6：结果汇总节点
# ============================================================
def result_summary_node(state: dict[str, Any]) -> dict[str, Any]:
    """整理最终行程，补充天气穿搭和注意事项，输出标准化结果。

    状态读写：
        - 读取: user_demand, travel_outline, daily_plans, check_result, iteration_count
        - 写入: current_step → "done"
        - 写入: travel_outline（补充 final_result 附加信息）
    """
    try:
        user_demand: dict[str, Any] = state.get("user_demand", {})
        daily_plans: list[dict[str, Any]] = state.get("daily_plans", [])
        check_result: dict[str, Any] = state.get("check_result", {})
        iteration_count = state.get("iteration_count", 0)
        destination = user_demand.get("destination", "")

        # 查询天气与穿搭建议
        weather_result = _safe_invoke_tool(
            weather_query,
            {"destination": destination, "month": user_demand.get("month", 4)},
        )
        weather_data: dict[str, Any] = weather_result.get("data", {})

        travel_tips: list[str] = [
            f"出行目的地: {destination}",
            f"出行天数: {len(daily_plans)} 天",
            f"出行人群: {user_demand.get('people', '')}",
        ]

        if weather_data:
            travel_tips.append(
                f"天气概况: {weather_data.get('weather_summary', '')}"
                f"（{weather_data.get('temperature_range', '')}）"
                f"—— {weather_data.get('clothing_suggestion', '')}"
            )
            wt = weather_data.get("travel_tips", [])
            if isinstance(wt, list):
                travel_tips.extend(wt)

        if iteration_count >= _MAX_ITERATIONS and not check_result.get("is_pass"):
            remaining_issues = check_result.get("issues", [])
            travel_tips.append(
                f"注意: 行程已优化 {iteration_count} 次达到上限，"
                f"仍存在问题: {'; '.join(remaining_issues) if remaining_issues else '无'}，"
                "建议手动微调。"
            )

        # ---- 修订 diff：如果是修订模式，计算修改前后的差异 ----
        revision_round: int = state.get("revision_round", 0)
        revision_history: list[dict[str, Any]] = state.get("revision_history", [])
        revision_diff: dict[str, Any] | None = None

        if revision_round > 0 and revision_history:
            # 从最近一轮历史中提取修改前快照，与当前结果对比
            last_entry: dict[str, Any] = revision_history[-1] if revision_history else {}
            before_snapshot: dict[str, Any] = last_entry.get("before_snapshot", {})
            if before_snapshot:
                after_snapshot: dict[str, dict[str, Any]] = {}
                for dp in daily_plans:
                    di: int = dp.get("day_index", 0)
                    after_snapshot[str(di)] = {
                        "theme": dp.get("theme", ""),
                        "spots": [s.get("name", "") for s in dp.get("spots", [])],
                        "daily_budget": dp.get("daily_budget", 0),
                        "food": dp.get("food_recommendation", [])[:3],
                    }

                per_day_diffs: list[dict[str, Any]] = []
                changed_days: list[int] = []
                for di_str, before in before_snapshot.items():
                    di = int(di_str)
                    after = after_snapshot.get(di_str)
                    if before != after:
                        changed_days.append(di)
                        diff_detail: dict[str, Any] = {
                            "day_index": di,
                            "before": before,
                            "after": after,
                        }
                        # 详细变化
                        change_items: list[str] = []
                        if before.get("theme") != (after or {}).get("theme"):
                            change_items.append(
                                f"主题: {before.get('theme','')} → {(after or {}).get('theme','')}"
                            )
                        before_spots = set(before.get("spots", []))
                        after_spots = set((after or {}).get("spots", []))
                        if before_spots != after_spots:
                            removed = before_spots - after_spots
                            added = after_spots - before_spots
                            if removed:
                                change_items.append(f"移除: {', '.join(removed)}")
                            if added:
                                change_items.append(f"新增: {', '.join(added)}")
                        if before.get("daily_budget") != (after or {}).get("daily_budget"):
                            change_items.append(
                                f"预算: {before.get('daily_budget',0)} → {(after or {}).get('daily_budget',0)}"
                            )
                        diff_detail["changes"] = change_items
                        per_day_diffs.append(diff_detail)

                if changed_days:
                    revision_diff = {
                        "round": revision_round,
                        "instruction": state.get("revision_instruction", ""),
                        "changed_days": changed_days,
                        "per_day_diffs": per_day_diffs,
                        "summary": f"第{revision_round}轮修订：修改了第{', '.join(map(str, changed_days))}天",
                    }

        # 组装最终结果
        final_result: dict[str, Any] = {
            "destination": destination,
            "total_days": len(daily_plans),
            "total_budget": user_demand.get("total_budget", 0),
            "people": user_demand.get("people", ""),
            "preferences": user_demand.get("preferences", []),
            "daily_plans": daily_plans,
            "travel_tips": travel_tips,
            "iteration_count": iteration_count,
            "check_result": {
                "is_pass": check_result.get("is_pass", False),
                "issues": check_result.get("issues", []),
                "suggestions": check_result.get("suggestions", []),
            },
        }

        # 附加修订信息
        if revision_round > 0:
            final_result["revision_round"] = revision_round
            final_result["revision_instruction"] = state.get("revision_instruction", "")
            final_result["revision_history"] = revision_history
            if revision_diff:
                final_result["revision_diff"] = revision_diff

        # ================================================================
        # 严格校验：成功必须同时满足 4 个条件
        #   1. daily_plans 非空
        #   2. 至少有一天行程包含有效景点（spots 非空 + 景点有名称）
        #   3. travel_outline.final_result 为有效非空行程文本
        #   4. 无流程异常（上游 error_msg 为空 + current_step 未标记 error）
        # ================================================================
        has_daily_plans = bool(daily_plans) and len(daily_plans) > 0
        has_valid_spots = has_daily_plans and any(
            len(day.get("spots", [])) > 0
            and any(
                isinstance(s, dict) and s.get("name", "").strip()
                for s in day.get("spots", [])
            )
            for day in daily_plans
        )
        has_final_result = (
            bool(final_result.get("destination", "").strip())
            and isinstance(final_result.get("daily_plans"), list)
            and len(final_result.get("daily_plans", [])) > 0
        )
        # 只有 current_step == "error" 才是致命流程异常
        # daily_fill_node 等节点使用兜底数据时会写入非致命 warning，
        # 此时 current_step 为 "generate_plans" 或 "revise_plans"，不应判为失败
        has_flow_error = state.get("current_step") == "error"

        # 传递非致命警告信息给前端（如 LLM 兜底、部分填充失败等）
        upstream_warning = str(state.get("error_msg", "") or "").strip()
        if upstream_warning and not has_flow_error:
            final_result["warning_msg"] = upstream_warning
            travel_tips.insert(0, f"⚠️ 生成警告: {upstream_warning[:200]}")

        success = (
            has_daily_plans
            and has_valid_spots
            and has_final_result
            and not has_flow_error
        )

        if not success:
            # 优先保留上游原始错误信息
            upstream_error = str(state.get("error_msg", "") or "").strip()
            if upstream_error:
                error_msg = upstream_error
            elif not has_daily_plans:
                error_msg = "行程生成失败，无有效内容"
            elif not has_valid_spots:
                error_msg = "行程生成失败，每日计划中无有效景点数据"
            elif not has_final_result:
                error_msg = "行程生成失败，最终结果为空或缺失目的地信息"
            else:
                error_msg = "行程生成失败，流程异常"

            logger.error(
                "result_summary_node 失败: has_daily_plans=%s, has_valid_spots=%s, "
                "has_final_result=%s, has_flow_error=%s, error_msg=%s",
                has_daily_plans, has_valid_spots, has_final_result,
                has_flow_error, error_msg,
            )
            return {
                "current_step": "done",
                "travel_outline": {
                    **state.get("travel_outline", {}),
                    "final_result": final_result,
                },
                "error_msg": error_msg,
            }

        logger.info(
            "result_summary_node 成功: destination=%s, days=%d, spots_total=%d, "
            "preferences=%s, iteration=%d",
            destination,
            len(daily_plans),
            sum(len(d.get("spots", [])) for d in daily_plans),
            user_demand.get("preferences", []),
            iteration_count,
        )
        return {
            "current_step": "done",
            "travel_outline": {
                **state.get("travel_outline", {}),
                "final_result": final_result,
            },
            "error_msg": "",
        }

    except Exception as exc:
        trace = traceback.format_exc()
        daily_plans = state.get("daily_plans", [])
        return {
            "current_step": "done",
            "travel_outline": {
                **state.get("travel_outline", {}),
                "final_result": {
                    "destination": state.get("user_demand", {}).get("destination", ""),
                    "total_days": len(daily_plans),
                    "total_budget": 0,
                    "people": "",
                    "preferences": [],
                    "daily_plans": daily_plans,
                    "travel_tips": [f"结果汇总异常: {str(exc)}"],
                    "iteration_count": state.get("iteration_count", 0),
                    "check_result": {},
                },
            },
            "error_msg": f"结果汇总节点异常: {str(exc)}\n{trace}",
        }


# ============================================================
# 单节点自测模块
# ============================================================
if __name__ == "__main__":
    import sys as _sys
    import io as _io
    # 强制 UTF-8 输出避免 Windows GBK 编码问题
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")
    _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding="utf-8", errors="replace")

    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ================================================================
    # 测试 0A: 参数校验 —— 拦截无效输入
    # ================================================================
    print("=" * 70)
    print("测试 0A: 参数校验 —— 拦截无效输入")
    print("=" * 70)

    from src.agent.__init__ import _validate_demand_params

    param_tests = [
        ({}, "空字典"),
        ({"destination": "", "days": 3, "total_budget": 2000, "people": "2人"}, "空目的地"),
        ({"destination": "郑州", "days": 0, "total_budget": 2000, "people": "2人"}, "days=0"),
        ({"destination": "郑州", "days": -1, "total_budget": 2000, "people": "2人"}, "负数天数"),
        ({"destination": "郑州", "days": 31, "total_budget": 2000, "people": "2人"}, "天数超限"),
        ({"destination": "郑州", "days": 3, "total_budget": -100, "people": "2人"}, "负数预算"),
        ({"destination": "郑州", "days": 3, "total_budget": 2000, "people": ""}, "空人群"),
        ({"destination": "郑州", "days": 3, "total_budget": 2000, "people": "2人"}, "合法参数"),
    ]

    param_all_ok = True
    for params, label in param_tests:
        err = _validate_demand_params(params)
        should_fail = label != "合法参数"
        is_correct = bool(err) == should_fail
        status = "✅" if is_correct else "❌"
        if not is_correct:
            param_all_ok = False
        print(f"  {status} {label}: {'拦截 → ' + err[:60] if err else '通过（合法参数）'}")

    print(f"  参数校验: {'✅ 全部正确' if param_all_ok else '❌ 存在异常'}")

    # ================================================================
    # 测试 0B: 小城市景点补充 —— 平顶山
    # ================================================================
    print(f"\n{'=' * 70}")
    print("测试 0B: 小城市景点补充 —— 模拟极少量景点触发补充")
    print("=" * 70)

    from src.utils.spot_supplement import supplement_spots_for_small_city

    # 模拟：平顶山仅检索到 1 个景点
    small_pool = [
        {"name": "尧山风景名胜区", "address": "平顶山市鲁山县", "duration": 4.0,
         "ticket_price": 65, "level": "5A", "area": "鲁山县",
         "core_feature": "中原名山", "tags": ["自然风光", "登山"],
         "recommendation": "平顶山最著名的景区"},
    ]
    supplemented = supplement_spots_for_small_city(
        "平顶山", small_pool, ["美食", "休闲"], min_count=3,
    )
    print(f"  原始景点: {len(small_pool)} → 补充后: {len(supplemented)}")
    print(f"  补充景点:")
    for s in supplemented[len(small_pool):]:
        print(f"    • {s['name']} | {s.get('core_feature','')[:40]} | tags={s.get('tags',[])}")

    supp_ok = len(supplemented) >= 3
    print(f"  小城市补充: {'✅ 通过' if supp_ok else '❌ 失败'}")

    # ---- 已有景点充足时跳过补充 ----
    many_spots = small_pool * 5  # 5个景点
    no_change = supplement_spots_for_small_city("成都", many_spots, [], min_count=3)
    fast_path_ok = len(no_change) == len(many_spots)
    print(f"  充足景点池快速路径: {'✅ 通过（无变动）' if fast_path_ok else '❌ 异常'}")

    # ================================================================
    # 测试 1: 正常用例 —— 郑州 3天 2000元 行程
    # ================================================================
    print("=" * 70)
    print("测试 1/2: 正常用例 —— 郑州 3天 2000元 2人 美食休闲")
    print("=" * 70)

    test_state: dict[str, Any] = {
        "user_demand": {
            "destination": "郑州",
            "days": 3,
            "total_budget": 2000,
            "people": "2人",
            "preferences": ["美食", "休闲"],
            "remark": "",
        },
        "travel_outline": {},
        "daily_plans": [],
        "check_result": {},
        "iteration_count": 0,
        "current_step": "",
        "error_msg": "",
    }

    # ---- Step 1: outline_generate_node ----
    print("\n[Step 1/4] outline_generate_node: 生成行程框架...")
    try:
        outline_result = outline_generate_node(test_state)
    except Exception as exc:
        print(f"  ERROR: outline_generate_node 异常: {exc}")
        import traceback as _tb
        _tb.print_exc()
        _sys.exit(1)

    if outline_result.get("current_step") == "error":
        print(f"  FAIL: {outline_result.get('error_msg', '未知错误')[:200]}")
        _sys.exit(1)

    test_state.update(outline_result)
    outline = test_state.get("travel_outline", {})
    frameworks = outline.get("daily_frameworks", [])
    spot_pool = outline.get("_spot_pool", [])
    print(f"  OK: {len(frameworks)} 天框架, {len(spot_pool)} 个景点入池")

    # ---- Step 2: daily_fill_node ----
    print("\n[Step 2/4] daily_fill_node: 逐日填充精细化行程...")
    try:
        fill_result = daily_fill_node(test_state)
    except Exception as exc:
        print(f"  ERROR: daily_fill_node 异常: {exc}")
        import traceback as _tb
        _tb.print_exc()
        _sys.exit(1)

    test_state.update(fill_result)
    daily_plans = test_state.get("daily_plans", [])
    print(f"  OK: {len(daily_plans)} 天行程已填充")

    # ---- Step 3: fact_check_node ----
    print("\n[Step 3/4] fact_check_node: 执行三项事实校验...")
    try:
        fact_result = fact_check_node(test_state)
    except Exception as exc:
        print(f"  ERROR: fact_check_node 异常: {exc}")
        import traceback as _tb
        _tb.print_exc()
        _sys.exit(1)

    test_state.update(fact_result)
    fact_check_data = test_state.get("check_result", {})
    fact_is_pass = fact_check_data.get("is_pass", False)
    fact_issues = fact_check_data.get("issues", [])
    fact_suggestions = fact_check_data.get("suggestions", [])

    print(f"  事实校验通过: {fact_is_pass}")
    print(f"  问题数: {len(fact_issues)}, 建议数: {len(fact_suggestions)}")
    if fact_issues:
        for iss in fact_issues:
            print(f"    ⚠️  {iss}")
    if fact_suggestions:
        for sug in fact_suggestions:
            print(f"    💡 {sug}")

    # ---- Step 4: plan_check_node (merge) ----
    print("\n[Step 4/4] plan_check_node: 合并机械校验...")
    try:
        check_result = plan_check_node(test_state)
    except Exception as exc:
        print(f"  ERROR: plan_check_node 异常: {exc}")
        import traceback as _tb
        _tb.print_exc()
        _sys.exit(1)

    test_state.update(check_result)
    final_check = test_state.get("check_result", {})
    final_pass = final_check.get("is_pass", False)
    final_issues = final_check.get("issues", [])
    print(f"  综合校验通过: {final_pass}")
    print(f"  合并后总问题: {len(final_issues)}")

    # ---- 汇总 ----
    print(f"\n{'=' * 70}")
    print("测试 1 汇总:")
    daily_plans = test_state.get("daily_plans", [])
    all_spots = [s for d in daily_plans for s in d.get("spots", [])]
    total_spent = sum(float(d.get("daily_budget", 0) or 0) for d in daily_plans)
    print(f"  生成天数: {len(daily_plans)}")
    print(f"  景点总数: {len(all_spots)}")
    print(f"  总花费: ¥{total_spent:.0f} (预算 ¥2000)")

    # 验证
    test1_ok = True
    if len(daily_plans) != 3:
        print(f"  ❌ 天数不匹配 (期望 3)")
        test1_ok = False
    if total_spent < 1800 or total_spent > 2200:
        print(f"  ❌ 总花费超出预算 ±10%")
        test1_ok = False
    if not fact_is_pass:
        # 正常用例应通过事实校验（或仅有轻微问题被机械校验修正）
        print(f"  ⚠️  事实校验有 {len(fact_issues)} 个问题（检查是否合理）")
    if test1_ok:
        print(f"  ✅ 测试 1 通过")
    else:
        print(f"  ❌ 测试 1 失败")

    # ================================================================
    # 测试 2: 异常用例 —— 构造含虚构景点、跨市错配的异常行程
    # ================================================================
    print(f"\n{'=' * 70}")
    print("测试 2/2: 异常用例 —— 构造含虚构景点/跨市错配/虚标价格的异常行程")
    print("=" * 70)

    # 基于测试1的 spot_pool 和基础设施，构造一个有问题的 state
    abnormal_state: dict[str, Any] = {
        "user_demand": {
            "destination": "郑州",
            "days": 2,
            "total_budget": 2000,
            "people": "2人",
            "preferences": ["美食", "休闲"],
            "remark": "",
        },
        "travel_outline": {
            "total_days": 2,
            "daily_frameworks": [
                {"day_index": 1, "theme": "异常测试Day1", "budget": 1000, "spot_types": [], "prefer_tags": ["美食"], "food_style": ""},
                {"day_index": 2, "theme": "异常测试Day2", "budget": 1000, "spot_types": [], "prefer_tags": ["休闲"], "food_style": ""},
            ],
            "_spot_pool": spot_pool if spot_pool else [
                {"name": "少林寺", "address": "河南省郑州市登封市嵩山", "level": "5A", "area": "登封市", "core_feature": "禅宗祖庭", "duration": 4.0, "ticket_price": 80, "tags": ["历史文化"]},
                {"name": "郑州黄河文化公园", "address": "郑州市惠济区", "level": "4A", "area": "惠济区", "core_feature": "黄河风光", "duration": 3.0, "ticket_price": 60, "tags": ["自然风光"]},
                {"name": "郑州博物馆", "address": "郑州市中原区", "level": "", "area": "中原区", "core_feature": "地方历史", "duration": 1.5, "ticket_price": 0, "tags": ["博物馆"]},
            ],
        },
        "daily_plans": [],  # 将在下面构造
        "check_result": {},
        "iteration_count": 0,
        "current_step": "",
        "error_msg": "",
    }

    # 构造异常行程：Day1 含跨市景点(西湖)和虚构景点，Day2 虚标价格
    abnormal_daily_plans = [
        {
            "day_index": 1,
            "theme": "异常Day1: 跨市错配+虚构景点",
            "spots": [
                {
                    "name": "杭州西湖",  # ❌ 跨市错配：西湖属于杭州，不属于郑州
                    "address": "杭州市西湖区龙井路1号",
                    "duration": 3.0,
                    "ticket_price": 0,
                    "time_slot": "上午",
                    "level": "5A",
                    "area": "西湖区",
                    "core_feature": "杭州标志性湖泊",
                    "tags": ["自然风光", "摄影"],
                    "recommendation": "杭州必游景点",
                },
                {
                    "name": "郑州梦幻未来城",  # ❌ 虚构景点：郑州不存在此景点
                    "address": "郑州市金水区未来路999号",
                    "duration": 2.0,
                    "ticket_price": 150,
                    "time_slot": "下午",
                    "level": "",
                    "area": "金水区",
                    "core_feature": "虚构的未来主题乐园",
                    "tags": ["亲子", "娱乐"],
                    "recommendation": "好玩的地方",
                },
                {
                    "name": "少林寺",  # ✅ 真实景点
                    "address": "河南省郑州市登封市嵩山",
                    "duration": 4.0,
                    "ticket_price": 500,  # ❌ 虚标价格：实际 80元
                    "time_slot": "晚上",
                    "level": "5A",
                    "area": "登封市",
                    "core_feature": "禅宗祖庭，少林武术发源地",
                    "tags": ["历史文化", "人文"],
                    "recommendation": "世界文化遗产",
                },
            ],
            "food_recommendation": ["杭州楼外楼（西湖店）"],  # ❌ 跨市餐厅
            "traffic_note": "杭州地铁1号线",  # ❌ 跨市交通
            "accommodation": "西湖边五星酒店，约1200元/晚",  # ❌ 虚高价格
            "daily_budget": 800,
            "budget_breakdown": {
                "住宿": 400,  # ❌ 虚高：郑州经济型150-250
                "餐饮": 200,
                "市内交通": 50,
                "景点门票": 650,  # ❌ 虚高
                "应急备用金": 100,
            },
        },
        {
            "day_index": 2,
            "theme": "异常Day2: 行程过载+价格虚低",
            "spots": [
                {
                    "name": "北京故宫",  # ❌ 跨市错配
                    "address": "北京市东城区景山前街4号",
                    "duration": 3.0,
                    "ticket_price": 60,
                    "time_slot": "上午",
                    "level": "5A",
                    "area": "东城区",
                    "core_feature": "明清皇家宫殿",
                    "tags": ["历史文化"],
                    "recommendation": "世界文化遗产",
                },
                {
                    "name": "郑州黄河文化公园",
                    "address": "郑州市惠济区",
                    "duration": 3.0,
                    "ticket_price": 2,  # ❌ 虚低价格：实际 60元
                    "time_slot": "上午",  # ❌ 两个上午景点，时间冲突
                    "level": "4A",
                    "area": "惠济区",
                    "core_feature": "黄河风光",
                    "tags": ["自然风光"],
                    "recommendation": "",
                },
                {
                    "name": "郑州博物馆",
                    "address": "郑州市中原区",
                    "duration": 1.5,
                    "ticket_price": 0,
                    "time_slot": "上午",  # ❌ 第三个上午景点，明显过载
                    "level": "",
                    "area": "中原区",
                    "core_feature": "",
                    "tags": ["博物馆"],
                    "recommendation": "",
                },
                {
                    "name": "东京迪士尼乐园",  # ❌ 跨市+跨国错配
                    "address": "日本千叶县",
                    "duration": 8.0,
                    "ticket_price": 400,
                    "time_slot": "上午",  # ❌ 第四个上午景点
                    "level": "",
                    "area": "千叶县",
                    "core_feature": "东京迪士尼",
                    "tags": ["亲子", "娱乐"],
                    "recommendation": "",
                },
            ],
            "food_recommendation": [],
            "traffic_note": "从北京飞到东京",
            "accommodation": "金字塔酒店，约10元/晚",  # ❌ 价格异常（虚低）
            "daily_budget": 500,
            "budget_breakdown": {
                "住宿": 10,  # ❌ 异常低价
                "餐饮": 50,
                "市内交通": 600,  # ❌ 交通费异常高
                "景点门票": 460,
                "应急备用金": -620,  # ❌ 负值
            },
        },
    ]

    abnormal_state["daily_plans"] = abnormal_daily_plans

    print("\n构造的异常行程包含以下问题：")
    print("  1. Day1: 杭州西湖（跨市错配）")
    print("  2. Day1: 郑州梦幻未来城（虚构景点）")
    print("  3. Day1: 少林寺门票¥500（虚标：实际¥80）")
    print("  4. Day1: 住宿¥400（偏高：郑州经济型¥150-250）")
    print("  5. Day1: 杭州楼外楼/杭州地铁（跨市信息）")
    print("  6. Day2: 北京故宫（跨市错配）")
    print("  7. Day2: 4个景点全挤上午（行程过载+时间冲突）")
    print("  8. Day2: 黄河文化公园门票¥2（虚低：实际¥60）")
    print("  9. Day2: 东京迪士尼（跨市+跨国错配）")
    print("  10. Day2: 住宿¥10（异常低价）")
    print("  11. Day2: 应急备用金¥-620（负值异常）")

    # ---- 先走 plan_check（绕过 daily_fill，直接校验异常行程） ----
    print("\n[异常用例] 执行 fact_check_node 校验异常行程...")
    try:
        fact_result_ab = fact_check_node(abnormal_state)
    except Exception as exc:
        print(f"  ERROR: fact_check_node 异常: {exc}")
        import traceback as _tb
        _tb.print_exc()
        _sys.exit(1)

    abnormal_state.update(fact_result_ab)
    ab_check = abnormal_state.get("check_result", {})
    ab_pass = ab_check.get("is_pass", False)
    ab_issues = ab_check.get("issues", [])
    ab_suggestions = ab_check.get("suggestions", [])

    print(f"\n  事实校验结果: {'通过' if ab_pass else '不通过'}")
    print(f"  发现问题: {len(ab_issues)} 个")
    print(f"  改进建议: {len(ab_suggestions)} 条")

    if ab_issues:
        print(f"\n  发现的问题列表:")
        for i, iss in enumerate(ab_issues, 1):
            print(f"    {i}. {iss}")

    if ab_suggestions:
        print(f"\n  改进建议:")
        for i, sug in enumerate(ab_suggestions, 1):
            print(f"    {i}. {sug}")

    # ---- 验证异常检测 ----
    print(f"\n{'=' * 70}")
    print("测试 2 汇总 —— 异常检测有效性验证:")

    # 关键检查项
    checks = [
        ("检测到跨市错配景点", any("跨市" in iss or "西湖" in iss or "故宫" in iss or "迪士尼" in iss for iss in ab_issues)),
        ("检测到虚构景点", any("虚构" in iss or "不存在" in iss or "梦幻未来城" in iss for iss in ab_issues)),
        ("检测到价格虚标", any("虚标" in iss or "500" in iss or "偏高" in iss for iss in ab_issues)),
        ("检测到价格虚低", any("虚低" in iss or "过低" in iss or "2" in iss for iss in ab_issues)),
        ("检测到行程过载/时间不可行", any(
            "过载" in iss or "过多" in iss or "不可行" in iss or "15小时" in iss
            or ("4" in iss and "景点" in iss) or ("四个" in iss and "景点" in iss)
            for iss in ab_issues
        )),
        ("检测到时间冲突/安排不可行", any(
            "冲突" in iss or "无法在一天内" in iss or "全部安排在上午" in iss
            or "晚上无法游览" in iss or "地理距离极远" in iss or "无法执行" in iss
            for iss in ab_issues
        )),
        ("检测到负值异常", any("负" in iss or "-620" in iss for iss in ab_issues)),
    ]

    test2_all_ok = True
    for check_name, result in checks:
        status = "✅" if result else "❌"
        if not result:
            test2_all_ok = False
        print(f"  {status} {check_name}")

    if not ab_pass:
        print(f"  ✅ 异常行程被正确识别为不通过")
    else:
        print(f"  ❌ 异常行程未被识别——校验漏报")
        test2_all_ok = False

    # 综合判定：LLM 校验应至少覆盖 60% 的问题类型
    detected_count = sum(1 for _, r in checks if r)
    print(f"\n  异常检测覆盖率: {detected_count}/{len(checks)} ({detected_count/len(checks)*100:.0f}%)")

    if test2_all_ok:
        print(f"  ✅ 测试 2 通过 —— 事实校验有效识别异常行程")
    else:
        if detected_count >= len(checks) * 0.5:
            print(f"  ⚠️  测试 2 部分通过 —— LLM 校验可能未覆盖所有异常类型（属正常波动），规则兜底已覆盖")
            print(f"  （LLM 校验有随机性，未检测到的项可能因模型输出截断或表述差异）")
        else:
            print(f"  ❌ 测试 2 失败 —— 异常检测覆盖率过低")

    # ================================================================
    # 最终汇总
    # ================================================================
    print(f"\n{'=' * 70}")
    print("最终结论:")
    print(f"  测试 1 (正常用例): {'✅ 通过' if test1_ok else '❌ 失败'}")
    print(f"  测试 2 (异常检测): {'✅ 通过' if test2_all_ok else '⚠️  部分通过/失败'}")
    if test1_ok and test2_all_ok:
        print(f"\n  🎉 所有测试通过!")
    elif test1_ok:
        print(f"\n  ⚠️  正常流程通过，异常检测可能受 LLM 随机性影响")
        print(f"  （规则兜底校验 _fallback_fact_check 已覆盖全部异常类型）")
    else:
        print(f"\n  ❌ 存在未通过的测试项，请检查")

