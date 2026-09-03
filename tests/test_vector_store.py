"""向量库工厂路由测试（不连真实数据库，只验证后端选择与维度传递）。"""
from __future__ import annotations

from app.config import Settings
from app.memory.factory import create_vector_store
from app.memory.vector_store import InMemoryVectorStore, PGVectorVectorStore
from app.storage.postgres import InMemoryRepository, PostgresRepository


def test_factory_routing_memory():
    s = Settings(_env_file=None)
    s.vector_store_backend = "memory"
    assert isinstance(create_vector_store(s, InMemoryRepository(), 256), InMemoryVectorStore)


def test_factory_routing_pgvector_requires_postgres():
    s = Settings(_env_file=None)
    s.vector_store_backend = "pgvector"
    # 内存仓库 + pgvector 后端 → 降级为内存向量库
    assert isinstance(create_vector_store(s, InMemoryRepository(), 256), InMemoryVectorStore)


def test_factory_routing_pgvector_dim():
    s = Settings(_env_file=None)
    s.vector_store_backend = "pgvector"
    # 引擎懒加载，不真正连接
    repo = PostgresRepository("postgresql+asyncpg://u:p@localhost:5432/db")
    store = create_vector_store(s, repo, 512)
    assert isinstance(store, PGVectorVectorStore)
    assert store._dim == 512
