"""pgvector 一键验证（在你本机跑，需先 `docker compose up -d` 启动 postgres+redis）。

验证：建表 → 建 pgvector 扩展 → 写入向量记忆 → 余弦检索排序 → 清理。
用法：python -m examples.verify_pgvector
"""
from __future__ import annotations

import asyncio

from app.config import get_settings
from app.graph.state import MemoryItem
from app.llm.embedder import HashEmbedder, create_embedder
from app.memory.factory import create_vector_store
from app.storage.postgres import PostgresRepository, create_repository


async def main() -> None:
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("请在 .env 配置 DATABASE_URL（postgresql+asyncpg://...）")

    repo = create_repository(settings.database_url)
    if not isinstance(repo, PostgresRepository):
        raise SystemExit("pgvector 验证需要 DATABASE_URL 指向 PostgreSQL")

    print(f"[1/5] 建表（sessions/users/chat_history/memories）...")
    await repo.init_db()

    print(f"[2/5] 加载嵌入模型（backend={settings.embedding_backend}）...")
    try:
        embedder = create_embedder(settings)
        await embedder.embed(["预热"])
    except Exception as exc:  # noqa: BLE001
        print(f"      真实嵌入模型不可用（{exc}），降级 hash 继续验证 pgvector 接线")
        embedder = HashEmbedder()
    print(f"      dim={embedder.dim}")

    print("[3/5] 建 pgvector 扩展 + memory_vectors 表...")
    vs = create_vector_store(settings, repo, embedder.dim)
    await vs.init()

    print("[4/5] 写入 3 条向量记忆并检索...")
    docs = [
        "用户喜欢三分糖的奶茶",
        "在一起纪念日：2023-05-20",
        "用户喜欢打篮球",
    ]
    embs = await embedder.embed(docs)
    for i, doc in enumerate(docs):
        await vs.upsert("verify-session", MemoryItem(content=doc, importance=0.7, category="preference"), embs[i])

    query_emb = (await embedder.embed(["你喜欢喝什么奶茶"], is_query=True))[0]
    hits = await vs.search("verify-session", query_emb, top_k=3, threshold=0.0)
    print("      检索结果（按相似度降序，奶茶应排第一）：")
    for mem, sim in hits:
        print(f"        [{sim:.4f}] {mem.content}")

    print("[5/5] 清理测试数据...")
    for mem, _ in hits:
        await vs.delete("verify-session", mem.id)
    print("DONE —— pgvector 全链路正常")

    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
