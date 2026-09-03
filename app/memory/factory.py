"""向量库工厂：按 settings.vector_store_backend 选择后端，维度由 embedder 决定。"""
from __future__ import annotations

import logging

from app.config import Settings
from app.memory.vector_store import (
    InMemoryVectorStore,
    MilvusVectorStore,
    PGVectorVectorStore,
    VectorStore,
)
from app.storage.postgres import PostgresRepository, Repository

logger = logging.getLogger(__name__)


def create_vector_store(settings: Settings, repo: Repository, dim: int = 256) -> VectorStore:
    backend = settings.vector_store_backend
    if backend == "pgvector":
        if not isinstance(repo, PostgresRepository):
            logger.warning("pgvector 后端需要 DATABASE_URL，降级为内存向量库")
            return InMemoryVectorStore()
        logger.info("使用 PGVector 向量库 (dim=%d)", dim)
        return PGVectorVectorStore(repo._engine, dim=dim)
    if backend == "milvus":
        logger.warning("使用 Milvus 向量库（实验性，需 pymilvus）(dim=%d)", dim)
        return MilvusVectorStore(dim=dim)
    logger.info("使用内置内存向量库（VECTOR_STORE_BACKEND=memory）")
    return InMemoryVectorStore()
