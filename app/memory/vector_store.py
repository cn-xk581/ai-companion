"""向量库抽象：长期记忆检索。

后端可插拔：memory（内置，无依赖）/ pgvector / milvus。
检索按 session 隔离，返回 (MemoryItem, 相似度)。
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

from app.graph.state import MemoryItem


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, session_id: str, memory: MemoryItem, embedding: list[float]) -> None: ...

    @abstractmethod
    async def search(
        self,
        session_id: str,
        embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> list[tuple[MemoryItem, float]]: ...

    @abstractmethod
    async def delete(self, session_id: str, memory_id: str) -> bool: ...

    @abstractmethod
    async def list_all(self, session_id: str) -> list[MemoryItem]: ...

    async def init(self) -> None:
        """服务启动时的初始化（建表/建集合）。默认无操作。"""
        return None

    async def close(self) -> None:  # noqa: B027
        return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class InMemoryVectorStore(VectorStore):
    """进程内向量库：dict[session_id][memory_id] -> (MemoryItem, embedding)。演示/测试默认。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, tuple[MemoryItem, list[float]]]] = {}

    async def upsert(self, session_id: str, memory: MemoryItem, embedding: list[float]) -> None:
        self._data.setdefault(session_id, {})[memory.id] = (memory, embedding)

    async def search(
        self,
        session_id: str,
        embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> list[tuple[MemoryItem, float]]:
        items = self._data.get(session_id, {})
        scored = [
            (mem, cosine_sim(embedding, emb))
            for mem, emb in items.values()
            if cosine_sim(embedding, emb) >= threshold
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    async def delete(self, session_id: str, memory_id: str) -> bool:
        bucket = self._data.get(session_id, {})
        if memory_id in bucket:
            del bucket[memory_id]
            return True
        return False

    async def list_all(self, session_id: str) -> list[MemoryItem]:
        return [mem for mem, _ in self._data.get(session_id, {}).values()]


class PGVectorVectorStore(VectorStore):
    """PGVector 实现：依赖 pgvector 扩展（docker-compose 已内置）。

    通过原始 SQL 使用 <=> 余弦距离算子；需要 pgvector Python 包（可选依赖 pgvector）。
    """

    def __init__(self, engine, dim: int = 256):
        from sqlalchemy.ext.asyncio import AsyncEngine

        self._engine: AsyncEngine = engine
        self._dim = dim
        self._ready = False

    async def init(self) -> None:
        """服务启动时建扩展 + 建表（幂等），失败快速暴露配置问题。"""
        await self._ensure_ready()

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            # pgvector 扩展（需要 pgvector/pgvector 镜像或已安装扩展的 PostgreSQL）
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS memory_vectors ("
                    "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, content TEXT NOT NULL, "
                    "importance FLOAT NOT NULL, category TEXT NOT NULL, tags JSONB DEFAULT '[]', "
                    f"embedding vector({self._dim}) NOT NULL)"
                )
            )
        # 注意：换 embedder 会改变向量维度，需手动 ALTER 或重建本表（README 已说明）
        self._ready = True

    async def upsert(self, session_id: str, memory: MemoryItem, embedding: list[float]) -> None:
        import json as _json

        from sqlalchemy import text

        await self._ensure_ready()
        emb_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO memory_vectors (id, session_id, content, importance, category, tags, embedding) "
                    "VALUES (:id, :sid, :content, :imp, :cat, CAST(:tags AS jsonb), CAST(:emb AS vector)) "
                    "ON CONFLICT (id) DO UPDATE SET content=EXCLUDED.content, importance=EXCLUDED.importance, "
                    "category=EXCLUDED.category, tags=EXCLUDED.tags, embedding=EXCLUDED.embedding"
                ),
                {
                    "id": memory.id,
                    "sid": session_id,
                    "content": memory.content,
                    "imp": memory.importance,
                    "cat": memory.category,
                    "tags": _json.dumps(memory.tags, ensure_ascii=False),
                    "emb": emb_str,
                },
            )

    async def search(
        self,
        session_id: str,
        embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> list[tuple[MemoryItem, float]]:
        from sqlalchemy import text

        await self._ensure_ready()
        emb_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, content, importance, category, tags, "
                        "(1 - (embedding <=> CAST(:emb AS vector))) AS sim "
                        "FROM memory_vectors WHERE session_id = :sid "
                        "ORDER BY embedding <=> CAST(:emb AS vector) LIMIT CAST(:k AS int)"
                    ),
                    {"sid": session_id, "emb": emb_str, "k": top_k},
                )
            ).mappings().all()
        out: list[tuple[MemoryItem, float]] = []
        for row in rows:
            if float(row["sim"]) < threshold:
                continue
            out.append(
                (
                    MemoryItem(
                        id=row["id"],
                        content=row["content"],
                        importance=float(row["importance"]),
                        category=row["category"],
                        tags=list(row["tags"] or []),
                    ),
                    float(row["sim"]),
                )
            )
        return out

    async def delete(self, session_id: str, memory_id: str) -> bool:
        from sqlalchemy import text

        await self._ensure_ready()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM memory_vectors WHERE id = :id AND session_id = :sid"),
                {"id": memory_id, "sid": session_id},
            )
            return result.rowcount > 0

    async def list_all(self, session_id: str) -> list[MemoryItem]:
        from sqlalchemy import text

        await self._ensure_ready()
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, content, importance, category, tags FROM memory_vectors "
                        "WHERE session_id = :sid ORDER BY importance DESC"
                    ),
                    {"sid": session_id},
                )
            ).mappings().all()
        return [
            MemoryItem(
                id=row["id"],
                content=row["content"],
                importance=float(row["importance"]),
                category=row["category"],
                tags=list(row["tags"] or []),
            )
            for row in rows
        ]


class MilvusVectorStore(VectorStore):
    """Milvus 实现（实验性）：需安装可选依赖 milvus=[\"pymilvus\"]。"""

    def __init__(self, uri: str = "http://localhost:19530", collection: str = "aigf_memories", dim: int = 256):
        self._uri = uri
        self._collection_name = collection
        self._dim = dim
        self._collection = None

    async def _init(self) -> None:
        if self._collection is not None:
            return
        try:
            from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("需要安装 pymilvus：pip install 'ai-girlfriend-agent[milvus]'") from exc

        connections.connect(alias="default", uri=self._uri)
        if utility.has_collection(self._collection_name):
            self._collection = Collection(self._collection_name)
        else:
            schema = CollectionSchema(
                [
                    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema(name="session_id", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=1024),
                    FieldSchema(name="importance", dtype=DataType.FLOAT),
                    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=32),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self._dim),
                ]
            )
            self._collection = Collection(self._collection_name, schema)
            self._collection.create_index("embedding", {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}})

    async def upsert(self, session_id: str, memory: MemoryItem, embedding: list[float]) -> None:
        await self._init()
        self._collection.upsert(
            [
                [memory.id],
                [session_id],
                [memory.content],
                [memory.importance],
                [memory.category],
                [embedding],
            ]
        )

    async def search(
        self, session_id: str, embedding: list[float], top_k: int = 5, threshold: float = 0.0
    ) -> list[tuple[MemoryItem, float]]:
        await self._init()
        self._collection.load()
        hits = self._collection.search(
            data=[embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            expr=f'session_id == "{session_id}"',
            output_fields=["id", "content", "importance", "category"],
        )
        out: list[tuple[MemoryItem, float]] = []
        for hit in hits[0]:
            if hit.distance < threshold:
                continue
            out.append(
                (
                    MemoryItem(
                        id=hit.entity.get("id"),
                        content=hit.entity.get("content"),
                        importance=float(hit.entity.get("importance")),
                        category=hit.entity.get("category"),
                    ),
                    float(hit.distance),
                )
            )
        return out

    async def delete(self, session_id: str, memory_id: str) -> bool:
        await self._init()
        self._collection.delete(f'id == "{memory_id}"')
        return True

    async def list_all(self, session_id: str) -> list[MemoryItem]:
        await self._init()
        self._collection.load()
        rows = self._collection.query(expr=f'session_id == "{session_id}"', output_fields=["id", "content", "importance", "category"])
        return [
            MemoryItem(
                id=r["id"],
                content=r["content"],
                importance=float(r["importance"]),
                category=r["category"],
            )
            for r in rows
        ]
