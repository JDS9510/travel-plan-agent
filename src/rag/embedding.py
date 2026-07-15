"""
嵌入模型封装 —— 基于 sentence-transformers 的 bge-small-zh 中文嵌入。

轻量级设计：
- 模型仅 ~24MB，首次自动下载，后续本地缓存
- 单例模式，全局复用 EmbeddingModel 实例
- 兼容 Chroma SentenceTransformerEmbeddingFunction 接口
"""

from __future__ import annotations

import threading
from typing import Optional

from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# bge-small-zh-v1.5: 轻量中文嵌入模型，512 维向量，C-MTEB 排名优秀
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# 全局单例 + 线程锁
_embedding_fn: Optional[SentenceTransformerEmbeddingFunction] = None
_lock = threading.Lock()


def get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    """获取全局嵌入函数实例（线程安全单例）。

    首次调用时自动下载模型（~24MB），后续调用直接返回缓存实例。

    Returns:
        SentenceTransformerEmbeddingFunction: Chroma 兼容的嵌入函数。

    Raises:
        ImportError: 当 sentence-transformers 未安装时。
        RuntimeError: 模型下载或加载失败时。
    """
    global _embedding_fn

    if _embedding_fn is not None:
        return _embedding_fn

    with _lock:
        if _embedding_fn is not None:
            return _embedding_fn

        try:
            _embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name=_MODEL_NAME,
            )
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers 未安装，请执行: pip install sentence-transformers"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"嵌入模型 '{_MODEL_NAME}' 加载失败: {str(exc)}"
            ) from exc

        return _embedding_fn


def get_embedding_dim() -> int:
    """返回嵌入向量维度（bge-small-zh 为 512）。"""
    return 512


def encode_texts(texts: list[str]) -> list[list[float]]:
    """编码文本列表为嵌入向量。

    Args:
        texts: 待编码的文本列表。

    Returns:
        list[list[float]]: 嵌入向量列表，每个向量 512 维。
    """
    fn = get_embedding_function()
    # Chroma 嵌入函数直接返回 list[list[float]]
    return fn(texts)
