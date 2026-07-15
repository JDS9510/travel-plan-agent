"""
向量知识库 —— 基于 Chroma 向量数据库的景点语义检索引擎。

功能：
- 启动时自动读取 data/spots.json 并向量化入库
- 支持增量更新（已存在的文档不重复入库）
- 语义相似度检索，替代原标签匹配，支持模糊偏好
- 线程安全的单例模式
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

import chromadb
from chromadb.api.types import EmbeddingFunction

from src.rag.embedding import get_embedding_function

# ============================================================
# 路径配置
# ============================================================
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SPOTS_PATH = os.path.join(_PROJECT_ROOT, "data", "spots.json")
_CHROMA_DIR = os.path.join(_PROJECT_ROOT, "data", "chroma_db")
_COLLECTION_NAME = "travel_spots"

# ============================================================
# 全局单例
# ============================================================
_store: Optional["VectorStore"] = None
_lock = threading.Lock()


class VectorStore:
    """Chroma 向量知识库封装。

    负责景点数据的向量化存储与语义检索，对外暴露简洁的检索接口。
    """

    def __init__(self) -> None:
        """初始化 Chroma 客户端与集合。

        使用持久化存储，数据保存在 data/chroma_db/ 目录。
        """
        os.makedirs(_CHROMA_DIR, exist_ok=True)

        self._embedding_fn: EmbeddingFunction = get_embedding_function()

        self._client: chromadb.PersistentClient = chromadb.PersistentClient(
            path=_CHROMA_DIR,
        )

        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"description": "旅行景点语义向量库"},
        )

        self._indexed: bool = self._collection.count() > 0

    # ----------------------------------------------------------
    # 索引管理
    # ----------------------------------------------------------
    @property
    def is_indexed(self) -> bool:
        """是否已完成索引。"""
        return self._indexed

    @property
    def document_count(self) -> int:
        """已索引文档数。"""
        return self._collection.count()

    def index_spots(self, force: bool = False) -> int:
        """从 data/spots.json 加载景点数据并向量化入库。

        Args:
            force: 为 True 时强制重建索引（清空后重新入库）。

        Returns:
            int: 本次新入库文档数。
        """
        if self._indexed and not force:
            return 0

        if not os.path.exists(_SPOTS_PATH):
            raise FileNotFoundError(f"景点数据文件不存在: {_SPOTS_PATH}")

        with open(_SPOTS_PATH, "r", encoding="utf-8") as f:
            spots_data: dict[str, list[dict[str, Any]]] = json.load(f)

        if force:
            self._collection.delete(where={})  # Chroma 清空集合
            self._indexed = False

        # 获取已存在的文档 ID 集合（增量更新）
        existing_ids: set[str] = set()
        if self._collection.count() > 0:
            existing_ids = set(self._collection.get()["ids"])

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for city, spots in spots_data.items():
            for i, spot in enumerate(spots):
                doc_id = f"{city}__{i}__{spot['name']}"
                if doc_id in existing_ids:
                    continue

                ids.append(doc_id)
                # 构造语义化文档文本：名称 + 标签 + 推荐理由
                doc_text = (
                    f"{spot['name']}。"
                    f"标签：{'、'.join(spot.get('tags', []))}。"
                    f"推荐理由：{spot.get('recommendation', '')}"
                )
                documents.append(doc_text)
                metadatas.append({
                    "city": city,
                    "name": spot["name"],
                    "address": spot.get("address", ""),
                    "duration": float(spot.get("duration", 0)),
                    "ticket_price": float(spot.get("ticket_price", 0)),
                    "tags": json.dumps(spot.get("tags", []), ensure_ascii=False),
                    "recommendation": spot.get("recommendation", ""),
                })

        if ids:
            self._collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

        self._indexed = True
        return len(ids)

    # ----------------------------------------------------------
    # 语义检索
    # ----------------------------------------------------------
    def search(
        self,
        query: str,
        destination: Optional[str] = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """语义检索景点。

        Args:
            query: 检索查询文本（如 "适合亲子的美食景点"）。
            destination: 可选，限定目的城市。
            top_k: 返回结果数，默认 10。

        Returns:
            list[dict]: 匹配景点列表，格式与原 spot_retriever 返回完全一致。
        """
        # 构造 where 过滤条件（限定城市）
        where_filter: Optional[dict[str, Any]] = None
        if destination:
            where_filter = {"city": destination}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(top_k, self._collection.count()),
                where=where_filter,
            )
        except Exception:
            # 查询失败时返回空列表，不中断流程
            return []

        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        spots: list[dict[str, Any]] = []
        seen: set[str] = set()

        metadatas_list = results.get("metadatas", [[]])
        distances_list = results.get("distances", [[]])

        for i, meta in enumerate(metadatas_list[0]):
            name = meta.get("name", "")
            if name in seen:
                continue
            seen.add(name)

            try:
                tags = json.loads(meta.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                tags = []

            spots.append({
                "name": name,
                "address": meta.get("address", ""),
                "duration": float(meta.get("duration", 0)),
                "ticket_price": float(meta.get("ticket_price", 0)),
                "tags": tags,
                "recommendation": meta.get("recommendation", ""),
                "_score": float(distances_list[0][i]) if distances_list[0] else 0.0,
            })

        return spots

    def search_by_preferences(
        self,
        destination: str,
        preferences: Optional[list[str]] = None,
        remark: str = "",
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """根据偏好标签构建语义查询并检索。

        Args:
            destination: 目的地城市。
            preferences: 偏好标签列表，如 ["美食", "亲子"]。
            remark: 补充要求，会拼入查询语句增强语义。
            top_k: 返回结果数。

        Returns:
            list[dict]: 匹配景点列表。
        """
        query_parts: list[str] = []

        if preferences:
            query_parts.append("、".join(preferences))
        if remark:
            query_parts.append(remark)

        # 无偏好时使用通用查询
        query = " ".join(query_parts) if query_parts else "热门景点 推荐"
        # 追加语义增强：模糊匹配
        query = f"寻找以下类型的景点：{query}"

        return self.search(query=query, destination=destination, top_k=top_k)

    # ----------------------------------------------------------
    # 城市匹配（兼容旧接口）
    # ----------------------------------------------------------
    def get_cities(self) -> list[str]:
        """返回已索引的城市列表。"""
        try:
            result = self._collection.get()
            if not result or not result.get("metadatas"):
                return []
            cities: set[str] = set()
            for meta in result["metadatas"]:
                city = meta.get("city", "")
                if city:
                    cities.add(city)
            return sorted(cities)
        except Exception:
            return []


# ============================================================
# 全局访问入口
# ============================================================
def get_vector_store() -> VectorStore:
    """获取全局 VectorStore 实例（线程安全单例）。

    首次调用时自动执行索引初始化。
    """
    global _store

    if _store is not None:
        return _store

    with _lock:
        if _store is not None:
            return _store

        _store = VectorStore()
        try:
            new_count = _store.index_spots()
            if new_count > 0:
                import logging
                logging.getLogger(__name__).info(
                    "向量库初始化完成，新增 %d 条索引", new_count
                )
        except Exception:
            # 索引失败不阻止启动，后续检索会返回空
            import logging
            logging.getLogger(__name__).warning(
                "向量库索引初始化失败，语义检索可能返回空结果"
            )

        return _store
