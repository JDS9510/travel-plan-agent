"""
结果缓存服务 —— 基于本地 LRU 缓存实现。

功能：
- 对相同行程需求（目的地/天数/预算/人群/偏好）做缓存
- 缓存 Key 由参数哈希生成，相同输入命中缓存直接返回
- LRU 淘汰策略 + TTL 过期时间，受控内存占用
- 预留 Redis 扩展接口（替换 _store / _get / _set 方法即可）
- 可通过环境变量 TRAVEL_CACHE_ENABLED 开关

线程安全：所有公开方法使用 threading.Lock 保护。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# 配置
# ---------------------------------------------------------------
_DEFAULT_MAX_SIZE = 128      # 最大缓存条目数
_DEFAULT_TTL = 3600          # 默认过期时间（秒），1 小时

# 缓存开关：优先读取 TRAVEL_PLANNER_ENABLE_CACHE，其次 TRAVEL_CACHE_ENABLED
_ENABLE_CACHE = os.getenv("TRAVEL_PLANNER_ENABLE_CACHE", "").strip().lower()
if _ENABLE_CACHE:
    _CACHE_ENABLED = _ENABLE_CACHE != "false"
else:
    _CACHE_ENABLED = os.getenv("TRAVEL_CACHE_ENABLED", "true").strip().lower() == "true"


# ---------------------------------------------------------------
# 缓存服务
# ---------------------------------------------------------------
class CacheService:
    """旅行行程结果缓存服务。

    基于 OrderedDict 的 LRU 缓存，支持 TTL 过期。
    使用方式：
        cache = CacheService()
        result = cache.get(demand_dict)
        if result is None:
            result = run_travel_planner(demand_dict)
            cache.set(demand_dict, result)
    """

    def __init__(
        self,
        max_size: int = _DEFAULT_MAX_SIZE,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        """初始化缓存服务。

        Args:
            max_size: 最大缓存条目数，默认 128。
            ttl: 缓存过期时间（秒），默认 3600。
        """
        # _cache: OrderedDict[key, (timestamp, result)]
        from collections import OrderedDict
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._enabled = _CACHE_ENABLED
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0

    # ---- 公开方法 ----

    def get(self, demand: dict[str, Any]) -> Optional[dict[str, Any]]:
        """查询缓存。

        Args:
            demand: 用户需求字典（同 run_travel_planner 入参）。

        Returns:
            dict | None: 缓存的行程结果，未命中返回 None。
        """
        if not self._enabled:
            return None

        key = self._make_key(demand)

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            timestamp, result = entry
            if time.time() - timestamp >= self._ttl:
                # 已过期
                del self._cache[key]
                self._misses += 1
                logger.info("缓存过期: key=%s", key)
                return None

            # LRU: 移到最后
            self._cache.move_to_end(key)
            self._hits += 1
            logger.info("缓存命中: key=%s (hits=%d, misses=%d, size=%d)",
                         key, self._hits, self._misses, len(self._cache))
            return result

    def set(self, demand: dict[str, Any], result: dict[str, Any]) -> None:
        """写入缓存。

        Args:
            demand: 用户需求字典。
            result: 生成的行程结果 dict。
        """
        if not self._enabled:
            return

        key = self._make_key(demand)

        with self._lock:
            self._cache[key] = (time.time(), result)
            self._cache.move_to_end(key)

            # LRU 淘汰
            while len(self._cache) > self._max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("LRU 淘汰: key=%s", evicted_key)

            logger.info("缓存写入: key=%s, size=%d/%d",
                         key, len(self._cache), self._max_size)

    def invalidate(self, demand: dict[str, Any]) -> bool:
        """使特定需求的缓存失效。

        Args:
            demand: 用户需求字典。

        Returns:
            bool: 是否成功删除。
        """
        key = self._make_key(demand)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.info("缓存失效: key=%s", key)
                return True
            return False

    def clear(self) -> int:
        """清空全部缓存。

        Returns:
            int: 被清除的条目数。
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            logger.info("缓存已全部清空: %d 条", count)
            return count

    # ---- 属性 ----

    @property
    def enabled(self) -> bool:
        """缓存是否启用。"""
        return self._enabled

    @property
    def size(self) -> int:
        """当前缓存条目数。"""
        with self._lock:
            return len(self._cache)

    @property
    def hit_rate(self) -> float:
        """缓存命中率（0.0 ~ 1.0）。"""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict[str, Any]:
        """缓存统计信息。"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self.hit_rate, 4),
            }

    # ---- 内部方法 ----

    @staticmethod
    def _make_key(demand: dict[str, Any]) -> str:
        """基于需求参数生成缓存键。

        键由 (destination, days, total_budget, people, sorted(preferences), remark)
        组合后 SHA256 哈希生成，确保相同参数生成相同 Key。

        Args:
            demand: 用户需求字典。

        Returns:
            str: 16 字符十六进制哈希。
        """
        key_data = {
            "destination": str(demand.get("destination", "")).strip(),
            "days": int(demand.get("days", 1)),
            "total_budget": float(demand.get("total_budget", 0)),
            "people": str(demand.get("people", "")).strip(),
            "preferences": sorted(demand.get("preferences", [])),
            "remark": str(demand.get("remark", "")).strip(),
        }
        raw = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------
_cache_service: Optional[CacheService] = None
_cache_lock = threading.Lock()


def get_cache_service() -> CacheService:
    """获取全局 CacheService 实例（线程安全单例）。"""
    global _cache_service
    if _cache_service is not None:
        return _cache_service

    with _cache_lock:
        if _cache_service is not None:
            return _cache_service
        _cache_service = CacheService()
        return _cache_service
