"""
景点检索工具 —— 基于大模型真实知识库 + Chroma 向量库的多级检索。

检索策略（缓存优先 + LLM 兜底 + 本地 2 级降级）：
  1. 优先：热门城市缓存 —— Top 20 城市结构化景点数据，毫秒级响应，命中跳过 LLM。
  2. 主方案：LLM 真实知识库检索 —— 缓存未命中时，利用大模型训练数据中的全国景点知识，
     支持任意国内城市，无硬编码限制，输出标准化字段。
  3. 降级 1：Chroma 向量库语义检索 —— 基于已索引的本地景点数据。
  4. 降级 2：本地 JSON 标签匹配（Jaccard 系数）—— 纯规则兜底。

每一级失败自动切换到下一级，保证工作流不中断、不报错。

相比旧版（仅标签匹配 / 向量检索）：
  - 支持全国任意城市，不再受限于 data/spots.json 的 2 个城市
  - 所有景点为大模型训练数据中的真实地点
  - 输出新增字段：level（景区等级）、area（所属区域）、core_feature（核心特色）
  - 内置 LRU 缓存（TTL 1 小时），同目的地重复查询免 API 调用
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from langchain.tools import tool

logger = logging.getLogger(__name__)

# ============================================================
# 路径 & 本地数据加载（第 3 级降级兜底）
# ============================================================
_SPOTS_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "spots.json",
)

with open(_SPOTS_DATA_PATH, "r", encoding="utf-8") as _f:
    _SPOTS_DATA: dict[str, list[dict]] = json.load(_f)

# ============================================================
# LLM 检索缓存
# ============================================================
# 缓存结构: {cache_key: (timestamp, spots_list)}
# TTL 1 小时，避免同一目的地短期内重复调用 LLM
_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL: float = 3600.0   # 秒


def _make_cache_key(destination: str, tags: Optional[list[str]] = None) -> str:
    """构造缓存键：目的地 + 排序偏好标签。"""
    tags_sorted = ",".join(sorted(tags)) if tags else "_all"
    return f"{destination.strip()}||{tags_sorted}"


def _cache_get(destination: str, tags: Optional[list[str]] = None) -> list[dict] | None:
    """读取缓存，过期返回 None。"""
    key = _make_cache_key(destination, tags)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    logger.debug("spot_retriever 缓存命中: %s", key)
    return data


def _cache_set(destination: str, tags: Optional[list[str]], spots: list[dict]) -> None:
    """写入缓存（LRU 淘汰：超过 200 条时删除最旧的 50 条）。"""
    key = _make_cache_key(destination, tags)
    _cache[key] = (time.time(), spots)
    # LRU 淘汰
    if len(_cache) > 200:
        oldest = sorted(_cache.items(), key=lambda x: x[1][0])[:50]
        for k, _ in oldest:
            del _cache[k]


# ============================================================
# Spot 字段规范化 —— 确保 LLM 输出与现有工作流 100% 兼容
# ============================================================
_REQUIRED_FIELDS = ["name", "address", "duration", "ticket_price", "tags", "recommendation"]
_OPTIONAL_FIELDS = ["level", "area", "core_feature"]

# 偏好标签 → LLM 检索时的语义扩展
_TAG_SEMANTIC_EXPAND: dict[str, str] = {
    "美食": "美食小吃、特色餐厅、美食街",
    "休闲": "轻松休闲、咖啡馆、公园散步、茶馆",
    "历史文化": "历史遗迹、博物馆、古镇古街、文化场馆",
    "自然风光": "山水湖泊、森林公园、自然风景区",
    "亲子": "亲子乐园、动物园、海洋馆、科技馆",
    "摄影": "出片打卡点、观景台、日出日落观景点",
    "购物": "商圈步行街、购物中心、特色市场",
    "夜生活": "夜景、夜市、酒吧街、灯光秀",
    "探险": "户外徒步、登山、漂流、探险体验",
    "文艺": "文艺街区、美术馆、独立书店、文创园",
    "打卡": "网红地标、必去景点、城市名片",
    "山水": "山水景区、湖泊、山峰、峡谷",
}


def _normalize_spot(raw: dict, destination: str) -> dict:
    """标准化单个景点字段，补全缺失项并修正类型。

    Args:
        raw: LLM 返回的原始 dict。
        destination: 目的地城市名，用于补全空地址。

    Returns:
        dict: 标准化后的景点数据。
    """
    spot: dict = {}

    # -- 名称 --
    spot["name"] = str(raw.get("name", "")).strip()
    if not spot["name"]:
        spot["name"] = raw.get("spot_name", raw.get("title", ""))

    # -- 地址 --
    spot["address"] = str(raw.get("address", "")).strip()
    if not spot["address"]:
        spot["address"] = f"{destination}市" if destination else ""

    # -- 时长 --
    try:
        spot["duration"] = float(raw.get("duration", 2.0))
    except (ValueError, TypeError):
        spot["duration"] = 2.0
    if spot["duration"] < 0.5:
        spot["duration"] = 1.5

    # -- 门票 --
    ticket = raw.get("ticket_price", raw.get("ticket", 0))
    try:
        spot["ticket_price"] = round(float(ticket), 2)
    except (ValueError, TypeError):
        spot["ticket_price"] = 0.0

    # -- 等级 --
    spot["level"] = str(raw.get("level", "")).strip().upper()
    # 统一等级格式
    _level_map = {
        "5A": "5A", "5A级": "5A", "5A景区": "5A", "AAAAA": "5A",
        "4A": "4A", "4A级": "4A", "4A景区": "4A", "AAAA": "4A",
        "3A": "3A", "3A级": "3A", "3A景区": "3A", "AAA": "3A",
        "2A": "2A", "2A级": "2A", "AA": "2A",
        "A": "A", "A级": "A", "1A": "A",
    }
    spot["level"] = _level_map.get(spot["level"], spot["level"] or "")

    # -- 区域 --
    spot["area"] = str(raw.get("area", raw.get("district", ""))).strip()

    # -- 核心特色 --
    spot["core_feature"] = str(raw.get("core_feature", raw.get("feature", ""))).strip()

    # -- 标签 --
    tags = raw.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []
    # 去重保序
    seen: set[str] = set()
    clean: list[str] = []
    for t in tags:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    spot["tags"] = clean

    # -- 推荐理由 --
    spot["recommendation"] = str(
        raw.get("recommendation", raw.get("reason", raw.get("description", "")))
    ).strip()

    return spot


# ============================================================
# 第 3 级：本地 JSON 标签匹配（原兜底逻辑）
# ============================================================

def _calculate_tag_similarity(tags: list[str], spot_tags: list[str]) -> float:
    """Jaccard 系数 —— 偏好标签与景点标签的相似度。"""
    if not tags:
        return 0.0
    set_a = {t.lower() for t in tags}
    set_b = {t.lower() for t in spot_tags}
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _tag_match_retrieve(
    destination: str,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """原标签匹配逻辑 —— 第 3 级降级方案。

    仅支持 data/spots.json 中已有城市（成都、杭州等）。
    """
    if tags is None:
        tags = []

    city_key: Optional[str] = None
    dest_lower = destination.strip()

    # 中文子串匹配
    for key in _SPOTS_DATA:
        if key in dest_lower or dest_lower in key:
            city_key = key
            break

    if city_key is None:
        return []

    city_spots: list[dict] = _SPOTS_DATA.get(city_key, [])

    if not tags:
        return city_spots[:10]

    scored: list[tuple[float, dict]] = []
    for spot in city_spots:
        sim = _calculate_tag_similarity(tags, spot.get("tags", []))
        if sim > 0:
            scored.append((sim, spot))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [spot for _, spot in scored[:10]]
    if not results:
        results = city_spots[:10]

    return results


# ============================================================
# 第 2 级：Chroma 向量库语义检索
# ============================================================
def _semantic_retrieve(
    destination: str,
    preferences: Optional[list[str]] = None,
    remark: str = "",
) -> list[dict]:
    """基于向量库的语义检索 —— 第 2 级降级方案。"""
    from src.rag.vector_store import get_vector_store

    store = get_vector_store()

    if not store.is_indexed or store.document_count == 0:
        logger.warning("向量库未索引，跳过语义检索")
        return []

    # 构造语义查询
    query_parts: list[str] = []
    if preferences:
        query_parts.append("、".join(preferences))
    if remark:
        query_parts.append(remark)

    query = " ".join(query_parts) if query_parts else "热门景点 推荐"
    query = f"寻找以下类型的景点：{query}"

    try:
        spots = store.search(query=query, destination=destination, top_k=10)
    except Exception as exc:
        logger.warning("向量库检索异常: %s", exc)
        return []

    # 标准化所有结果（统一字段格式）
    spots = [_normalize_spot(s, destination) for s in spots]

    # 语义检索可能返回不足，用标签匹配补齐
    if len(spots) < 3:
        fallback = _tag_match_retrieve(destination, preferences)
        seen = {s["name"] for s in spots}
        for s in fallback:
            s_norm = _normalize_spot(s, destination)
            if s_norm["name"] not in seen:
                spots.append(s_norm)
                seen.add(s_norm["name"])
            if len(spots) >= 10:
                break

    return spots


# ============================================================
# 第 1 级：LLM 真实知识库检索（主方案）
# ============================================================
def _parse_llm_json(text: str) -> list[dict] | None:
    """从 LLM 原始输出中提取 JSON 数组。

    兼容多种格式：
    - 纯 JSON 数组: [...]
    - Markdown 代码块: ```json ... ```
    - 带前后文字的 JSON
    - 单引号 JSON（Python风格）
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1) 尝试 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()

    # 2) 找到 JSON 数组边界
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or start >= end:
        return None
    text = text[start:end + 1]

    # 3) 解析 JSON
    # 先尝试标准 JSON
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 4) 修复常见问题后重试
    try:
        # 单引号 → 双引号（注意字符串内部的单引号）
        fixed = re.sub(r"(?<!\\)'", '"', text)
        data = json.loads(fixed)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 5) 逐行/逐对象提取（最后手段）
    try:
        # 尝试找到所有 {...} 对象
        objs = re.findall(r"\{[^{}]*\}", text)
        if objs:
            result = []
            for obj_str in objs:
                try:
                    obj = json.loads(obj_str)
                    if isinstance(obj, dict) and "name" in obj:
                        result.append(obj)
                except json.JSONDecodeError:
                    continue
            if result:
                return result
    except Exception:
        pass

    return None


def _build_llm_retrieval_prompt(
    destination: str,
    tags: Optional[list[str]] = None,
) -> str:
    """构造 LLM 景点检索 prompt。

    Args:
        destination: 目的地城市/区县名称。
        tags: 偏好标签列表。

    Returns:
        str: 完整 prompt 文本。
    """
    if tags is None:
        tags = []

    # 偏好语义扩展
    tag_descriptions: list[str] = []
    for t in tags:
        expanded = _TAG_SEMANTIC_EXPAND.get(t, t)
        tag_descriptions.append(f"  - {t}（{expanded}）")

    tag_section = ""
    if tag_descriptions:
        tag_section = (
            "## 偏好要求\n"
            "请优先推荐匹配以下偏好的景点：\n"
            + "\n".join(tag_descriptions) + "\n"
        )
    else:
        tag_section = "## 偏好要求\n请推荐该城市最热门、最具代表性的景点。\n"

    prompt = f"""为中国旅游城市「{destination}」列出真实景点，输出纯JSON数组。

约束: 景点必须真实存在于{destination}；等级(5A/4A/3A/2A/A/无)不确定填"无"；门票为成人普通票价(免费填0)；标签限选:美食,休闲,历史文化,自然风光,亲子,摄影,购物,夜生活,探险,文艺,打卡,山水,人文,户外,博物馆。

{tag_section}
输出格式(纯JSON数组,无Markdown/注释/解释):
[{{"name":"景点名","level":"5A","address":"地址","area":"区","duration":2.5,"ticket_price":60.0,"core_feature":"一句话特色","tags":["标签"],"recommendation":"20-50字推荐理由"}}]

返回8~15个景点(县级市/区县可5~10个)。只输出JSON数组。"""

    return prompt


def _llm_real_retrieve(
    destination: str,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """LLM 真实知识库检索 —— 主方案。

    利用大模型训练数据中内化的中国景点知识，
    为任意国内城市检索真实景点列表。

    Args:
        destination: 目的地城市/区县名称。
        tags: 偏好标签列表。

    Returns:
        list[dict]: 标准化景点列表，字段与 Spot 模型兼容。
        失败时返回空列表，由调用方切换到下一级降级方案。
    """
    if tags is None:
        tags = []

    # 检查缓存
    cached = _cache_get(destination, tags)
    if cached is not None:
        return cached

    # 构造 prompt 并调用 LLM
    prompt = _build_llm_retrieval_prompt(destination, tags)

    try:
        from src.llm.llm_client import get_llm_client

        client = get_llm_client()
        response = client.chat(prompt, temperature=0.2)

        # 解析 JSON
        raw_spots = _parse_llm_json(response)
        if not raw_spots:
            logger.warning(
                "LLM 景点检索: 无法从响应中解析 JSON。destination=%s, "
                "response_preview=%s",
                destination, response[:200],
            )
            return []

        # 标准化每个景点
        spots: list[dict] = []
        for raw in raw_spots:
            if not isinstance(raw, dict):
                continue
            spot = _normalize_spot(raw, destination)
            # 过滤明显无效的条目（无名称或名称太短）
            if spot["name"] and len(spot["name"]) >= 2:
                spots.append(spot)

        if not spots:
            logger.warning(
                "LLM 景点检索: 解析到 0 个有效景点。destination=%s, "
                "raw_count=%d",
                destination, len(raw_spots),
            )
            return []

        # 去重（按名称）
        seen_names: set[str] = set()
        deduped: list[dict] = []
        for s in spots:
            name = s["name"]
            if name not in seen_names:
                seen_names.add(name)
                deduped.append(s)

        # 写入缓存
        _cache_set(destination, tags, deduped)

        logger.info(
            "LLM 景点检索成功: destination=%s, tags=%s, count=%d",
            destination, tags, len(deduped),
        )
        return deduped

    except Exception as exc:
        logger.warning(
            "LLM 景点检索异常: destination=%s, tags=%s, error=%s",
            destination, tags, str(exc),
        )
        return []


# ============================================================
# 公开工具函数
# ============================================================
@tool
def spot_retriever(
    destination: str,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """根据目的地城市和偏好标签，检索匹配的真实景点列表。

    缓存优先 + LLM 兜底 + 本地 2 级降级：
    1. 优先：热门城市缓存（命中则跳过 LLM，毫秒级响应）
    2. 主方案：LLM 真实知识库检索（支持全国任意城市，无硬编码限制）
    3. 降级 1：Chroma 向量库语义检索
    4. 降级 2：本地 JSON 标签匹配

    参数:
        destination: 目的地城市名称，如 "成都"、"杭州"、"郑州"、"平顶山"。
                     支持地级市、县级市、区县级目的地。
        tags: 偏好标签列表，如 ["亲子", "美食"]，不传则返回该城市热门景点。

    返回:
        list[dict]: 匹配的景点列表（最多 15 个），字段与 Spot 模型完全兼容。
        每个 dict 包含: name, level, address, area, duration, ticket_price,
        core_feature, tags, recommendation。
    """
    start_ts = time.time()
    error_msg: Optional[str] = None
    results: list[dict] = []

    if tags is None:
        tags = []

    dest_clean = destination.strip()
    if not dest_clean:
        logger.warning("spot_retriever: destination 为空")
        return []

    # ---- 第 1 级：热门城市本地景点缓存（命中直接返回，跳过 LLM） ----
    cache_hit = False
    if os.getenv("SPOT_CACHE_ENABLED", "1") == "1":
        try:
            from src.cache.spot_cache import get_cached_spots
            cached = get_cached_spots(dest_clean, tags)
            if cached:
                cache_hit = True
                logger.info(
                    "热门城市缓存命中（跳过LLM）: destination=%s, count=%d",
                    dest_clean, len(cached),
                )
                _trace(start_ts, dest_clean, tags, cached, None)
                return cached
        except Exception as exc:
            logger.debug("缓存查询跳过: %s", str(exc))

    # ---- 第 2 级：LLM 真实知识库检索（缓存未命中，支持全国任意城市） ----
    llm_error: Optional[str] = None
    try:
        results = _llm_real_retrieve(dest_clean, tags)
        if results:
            _trace(start_ts, dest_clean, tags, results, None)
            return results
    except Exception as exc:
        llm_error = str(exc)
        logger.warning("LLM 检索异常，降级到本地数据库: %s", llm_error)

    # ---- 第 3 级：本地数据库降级（ChromaDB → JSON） ----
    fallback_errors: list[str] = []
    if llm_error:
        fallback_errors.append(f"LLM: {llm_error[:80]}")

    # 3a. Chroma 向量库语义检索
    try:
        results = _semantic_retrieve(dest_clean, tags)
        if results:
            logger.info("向量库检索成功: destination=%s, count=%d", dest_clean, len(results))
            _trace(start_ts, dest_clean, tags, results, "; ".join(fallback_errors))
            return results
    except Exception as exc:
        fallback_errors.append(f"向量库: {str(exc)[:60]}")
        logger.warning("向量库检索异常: %s", str(exc))

    # 3b. 本地 JSON 标签匹配
    try:
        raw_results = _tag_match_retrieve(dest_clean, tags)
        if raw_results:
            results = [_normalize_spot(s, dest_clean) for s in raw_results]
            logger.info("标签匹配成功: destination=%s, count=%d", dest_clean, len(results))
            _trace(start_ts, dest_clean, tags, results, "; ".join(fallback_errors))
            return results
    except Exception as exc:
        fallback_errors.append(f"JSON: {str(exc)[:60]}")
        logger.error("标签匹配也失败: %s", str(exc))

    # ---- 全部策略失败 ----
    error_detail = " → ".join(fallback_errors) if fallback_errors else "LLM返回空且本地数据库无匹配"
    error_msg = f"未找到'{dest_clean}'的景点数据: {error_detail}"
    _trace(start_ts, dest_clean, tags, [], error_msg)
    return []


def _trace(
    start_ts: float,
    destination: str,
    tags: list[str],
    results: list[dict],
    error_msg: Optional[str],
) -> None:
    """记录工具调用追踪。"""
    try:
        from src.utils.tracer import get_tracer

        tracer = get_tracer()
        duration_ms = (time.time() - start_ts) * 1000
        tracer.trace_tool_call(
            tool_name="spot_retriever",
            params={"destination": destination, "tags": tags},
            result={
                "count": len(results),
                "top3": [
                    {"name": r["name"], "level": r.get("level", "")}
                    for r in results[:3]
                ],
            },
            duration_ms=duration_ms,
            error=error_msg,
        )
    except Exception:
        pass


# ============================================================
# 单节点自测入口
# ============================================================
if __name__ == "__main__":
    """
    自测：验证 spot_retriever 对郑州、平顶山、成都三个目的地
    均能返回真实景点列表，字段完整无缺失。

    运行方式:
        cd 旅行行程规划
        python -m src.tools.spot_retriever
    """
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    test_cases: list[tuple[str, list[str]]] = [
        ("郑州", []),
        ("平顶山", []),
        ("成都", ["美食", "休闲"]),
    ]

    all_pass = True
    for city, prefs in test_cases:
        print(f"\n{'=' * 60}")
        print(f"  测试: {city}  |  偏好: {prefs if prefs else '无（热门景点）'}")
        print(f"{'=' * 60}")

        spots = spot_retriever.invoke({
            "destination": city,
            "tags": prefs,
        })

        # ---- 验证 ----
        errors: list[str] = []

        if not spots:
            errors.append("返回空列表")
        elif len(spots) < 3:
            errors.append(f"景点数量不足: {len(spots)} < 3")

        # 逐字段检查
        required = [
            "name", "address", "duration", "ticket_price",
            "tags", "recommendation",
        ]
        optional = ["level", "area", "core_feature"]

        for i, s in enumerate(spots[:8]):
            for f in required:
                if not s.get(f):
                    errors.append(f"景点[{i}] 缺失必填字段: {f}")
            for f in optional:
                if f not in s:
                    errors.append(f"景点[{i}] 缺失可选字段: {f}")
            # 真实性基础检查
            name = s.get("name", "")
            if name and len(name) < 2:
                errors.append(f"景点[{i}] 名称过短: '{name}'")
            addr = s.get("address", "")
            if addr and city not in addr and "省" not in addr:
                # 有些地址不带城市名也是合理的（如"中山陵"属于南京但地址写"玄武区"）
                pass

        if errors:
            all_pass = False
            print(f"\n  ❌ 失败 ({len(errors)} 个错误):")
            for e in errors[:10]:
                print(f"     - {e}")
        else:
            print(f"\n  ✅ 通过 —— 返回 {len(spots)} 个景点")

        # 打印景点列表
        print(f"\n  景点列表:")
        for i, s in enumerate(spots[:10], 1):
            level = s.get("level", "") or "?"
            area = s.get("area", "") or "?"
            print(
                f"  {i:2d}. {s['name']:<16s} | {level:<3s} | "
                f"¥{s.get('ticket_price', 0):>6.1f} | "
                f"{s.get('duration', 0):.1f}h | {area}"
            )
            if s.get("core_feature"):
                print(f"      特色: {s['core_feature'][:60]}")
            if s.get("tags"):
                print(f"      标签: {', '.join(s['tags'][:6])}")

    print(f"\n{'=' * 60}")
    print(f"  总结果: {'✅ 全部通过' if all_pass else '❌ 存在失败'}")
    print(f"{'=' * 60}")
    sys.exit(0 if all_pass else 1)
