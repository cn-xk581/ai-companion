"""节点 1：memory_retrieve_node 记忆检索。

根据当前用户 query 去向量库检索本 session 的相关长期记忆，
按 importance 权重过滤取 top-N，注入 retrieved_memories。
不把全部历史丢进 prompt，解决上下文爆炸。
"""
from __future__ import annotations

import logging

from app.graph.context import GraphContext
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


async def memory_retrieve_node(state: AgentState, ctx: GraphContext) -> dict:
    query = state.user_input.strip()
    if not query:
        return {"retrieved_memories": []}
    try:
        embedding = (await ctx.embedder.embed([query], is_query=True))[0]
        results = await ctx.vector_store.search(
            state.session_id,
            embedding,
            top_k=ctx.settings.memory_retrieve_top_k,
            threshold=ctx.settings.memory_importance_threshold,
        )
        # 相似度 + importance 加权排序，取更相关的片段
        scored = sorted(
            results,
            key=lambda item: 0.6 * item[1] + 0.4 * item[0].importance,
            reverse=True,
        )
        memories = [m for m, _ in scored]
        if memories:
            logger.info("[%s] retrieved %d memories", state.session_id, len(memories))
        return {"retrieved_memories": memories}
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory retrieve failed: %s", exc)
        return {"retrieved_memories": []}
