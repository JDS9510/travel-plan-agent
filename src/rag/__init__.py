"""
RAG 向量知识库包 —— 统一导出语义检索接口。

使用方式:
    from src.rag import get_vector_store, search_spots

    store = get_vector_store()
    results = store.search_by_preferences("成都", ["美食", "亲子"])
"""

from __future__ import annotations

from src.rag.embedding import get_embedding_function, encode_texts
from src.rag.vector_store import VectorStore, get_vector_store

__all__ = [
    "VectorStore",
    "get_vector_store",
    "get_embedding_function",
    "encode_texts",
]
