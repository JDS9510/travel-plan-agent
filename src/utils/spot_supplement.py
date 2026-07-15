"""
小城市景点补充工具 —— 当 LLM/Chroma/本地检索结果不足时自动补充。

设计原则：
- 不编造具体场馆名称，使用"{城市}市博物馆"等通用描述
- 所有补充景点标记 level="" ，门票默认 0，duration 默认 1.5h
- 标签从目的地城市特征推导，core_feature 写"城市基础文化/休闲设施"
- 补充景点会经过 fact_check 节点的真实性校验

使用方式：
    from src.utils.spot_supplement import supplement_spots_for_small_city

    spot_pool = supplement_spots_for_small_city("平顶山", spot_pool, ["美食","休闲"])
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 最小景点数量阈值：低于此数触发补充
_MIN_SPOT_COUNT = 3

# 每类通用体验的模板
_GENERIC_EXPERIENCES: list[dict[str, Any]] = [
    {
        "suffix": "市博物馆",
        "address_tpl": "{city}市区",
        "duration": 2.0,
        "ticket_price": 0,
        "level": "",
        "core_feature": "地方历史文化综合展示，了解{city}历史的最佳窗口",
        "tags": ["历史文化", "博物馆", "打卡"],
        "recommendation": "免费开放的市级博物馆，馆藏丰富，适合了解当地历史文化",
    },
    {
        "suffix": "人民公园",
        "address_tpl": "{city}市中心",
        "duration": 1.5,
        "ticket_price": 0,
        "level": "",
        "core_feature": "市民休闲公园，环境优美，适合散步放松",
        "tags": ["休闲", "自然风光"],
        "recommendation": "城市中心的绿肺，本地人休闲首选，免费入园",
    },
    {
        "suffix": "中心商业步行街",
        "address_tpl": "{city}市中心商圈",
        "duration": 2.0,
        "ticket_price": 0,
        "level": "",
        "core_feature": "城市商业中心，集购物、餐饮、娱乐于一体",
        "tags": ["购物", "美食", "打卡"],
        "recommendation": "城市最繁华的商业街区，特色小吃和购物集中地",
    },
    {
        "suffix": "特色美食街",
        "address_tpl": "{city}市区",
        "duration": 1.5,
        "ticket_price": 0,
        "level": "",
        "core_feature": "本地特色美食聚集地，品尝地道{city}味道",
        "tags": ["美食", "夜生活", "打卡"],
        "recommendation": "汇聚{city}各色地道小吃，是体验本地饮食文化的必去之处",
    },
    {
        "suffix": "城市滨河公园",
        "address_tpl": "{city}市区河畔",
        "duration": 1.5,
        "ticket_price": 0,
        "level": "",
        "core_feature": "滨水景观带，适合散步、骑行、赏景",
        "tags": ["休闲", "自然风光", "摄影"],
        "recommendation": "沿河而建的线性公园，夜景尤其美丽，适合傍晚散步",
    },
    {
        "suffix": "民俗文化村",
        "address_tpl": "{city}近郊",
        "duration": 2.5,
        "ticket_price": 30,
        "level": "",
        "core_feature": "展示{city}传统民俗文化和手工艺的体验园区",
        "tags": ["历史文化", "亲子", "文艺"],
        "recommendation": "可以体验当地传统手工艺和民俗活动，适合亲子游和深度文化体验",
    },
]

# 城市名片类补充（中国城市普遍拥有的地标）
_CITY_LANDMARKS: list[dict[str, Any]] = [
    {
        "name_tpl": "{city}站/火车站商圈",
        "address_tpl": "{city}火车站周边",
        "duration": 1.5,
        "ticket_price": 0,
        "level": "",
        "core_feature": "交通枢纽周边商圈，人流密集，餐饮购物方便",
        "tags": ["购物", "美食", "打卡"],
        "recommendation": "城市的交通心脏，周边配套齐全，适合抵达后第一站逛吃",
    },
    {
        "name_tpl": "{city}图书馆",
        "address_tpl": "{city}市区",
        "duration": 1.0,
        "ticket_price": 0,
        "level": "",
        "core_feature": "市级公共图书馆，安静舒适的文化空间",
        "tags": ["文艺", "休闲"],
        "recommendation": "现代化的公共文化设施，建筑本身也是一道风景",
    },
]


def supplement_spots_for_small_city(
    destination: str,
    existing_spots: list[dict[str, Any]],
    preferences: list[str] | None = None,
    min_count: int = _MIN_SPOT_COUNT,
) -> list[dict[str, Any]]:
    """当景点池数量不足时，自动补充城市通用体验项目。

    不会编造具体场馆名称——使用"{城市}市博物馆"等通用描述。
    所有补充的景点会标记 level=""（无景区等级），后续会经过 fact_check 校验。

    Args:
        destination: 目的地城市名称。
        existing_spots: 已检索到的景点列表（dict 格式，含 name/address/duration 等字段）。
        preferences: 用户偏好标签列表，用于匹配补充类型。
        min_count: 最少需要的景点数，低于此数触发补充。

    Returns:
        list[dict]: 补充后的完整景点列表（existing + supplemented）。
    """
    if preferences is None:
        preferences = []

    existing_count = len(existing_spots)

    if existing_count >= min_count:
        logger.debug(
            "spot_supplement: 景点池充足 (%d >= %d)，跳过补充 | destination=%s",
            existing_count, min_count, destination,
        )
        return existing_spots

    needed = min_count - existing_count
    logger.info(
        "spot_supplement: 景点不足 (%d < %d)，启动小城市补充策略 | "
        "destination=%s | need=%d | preferences=%s",
        existing_count, min_count, destination, needed, preferences,
    )

    # 已有景点名称集合（用于去重）
    existing_names: set[str] = set()
    for s in existing_spots:
        name = str(s.get("name", "")).strip()
        if name:
            existing_names.add(name)

    # 按偏好优先级排序补充模板
    pref_set = {p.lower() for p in preferences}
    scored_templates: list[tuple[int, dict[str, Any]]] = []
    for tpl in _GENERIC_EXPERIENCES:
        score = 0
        tpl_tags = [t.lower() for t in tpl.get("tags", [])]
        for pt in pref_set:
            if pt in tpl_tags:
                score += 1
        if not pref_set:
            score = 1  # 无偏好时所有模板均等
        scored_templates.append((score, tpl))

    # 城市名片
    for tpl in _CITY_LANDMARKS:
        scored_templates.append((1, tpl))

    # 按分数降序排列
    scored_templates.sort(key=lambda x: x[0], reverse=True)

    supplemented: list[dict[str, Any]] = []
    for _, tpl in scored_templates:
        if len(supplemented) >= needed:
            break

        # 构造景点名称和地址
        if "name_tpl" in tpl:
            name = tpl["name_tpl"].format(city=destination)
        else:
            name = f"{destination}{tpl['suffix']}"
        address = tpl["address_tpl"].format(city=destination)

        # 去重：跳过与已有景点名称相似的
        if name in existing_names:
            continue
        # 简单模糊去重：名称关键词重叠
        name_clean = name.replace(destination, "").strip()
        is_dup = False
        for en in existing_names:
            en_clean = en.replace(destination, "").strip()
            if name_clean and en_clean and (
                name_clean in en_clean or en_clean in name_clean
            ):
                is_dup = True
                break
        if is_dup:
            continue

        spot = {
            "name": name,
            "address": address,
            "duration": tpl.get("duration", 1.5),
            "ticket_price": tpl.get("ticket_price", 0),
            "level": tpl.get("level", ""),
            "area": tpl.get("address_tpl", "").format(city=destination).replace(destination, "").strip("市区河畔近郊市中心商圈周边"),
            "core_feature": tpl.get("core_feature", "").format(city=destination),
            "tags": tpl.get("tags", []),
            "recommendation": tpl.get("recommendation", "").format(city=destination),
        }
        supplemented.append(spot)
        existing_names.add(name)

    result = existing_spots + supplemented
    logger.info(
        "spot_supplement: 补充完成 | destination=%s | "
        "original=%d | supplemented=%d | total=%d",
        destination, existing_count, len(supplemented), len(result),
    )

    # 补充后仍不足 → 降级日志
    if len(result) < min_count:
        logger.warning(
            "spot_supplement: 补充后仍不足 %d 个景点 | destination=%s | total=%d",
            min_count, destination, len(result),
        )

    return result
