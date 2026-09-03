"""节点 8：persist_node 持久化（内联等待，保证「先落库、再回包」，杜绝丢数据）。

- chat_history 写入 PostgreSQL
- new_memories 写入向量库 + 关系数据库
- 更新 session 的 relationship_stage / relationship_score / current_mood / persona 持久化
- 更新 Redis 会话缓存

同时把推进后的 relationship_score / relationship_stage 回写 state，
让调用方拿到的响应反映最新关系状态。
"""
from __future__ import annotations

import json
import logging

from app.graph.context import GraphContext
from app.graph.state import AgentState, ChatMessage
from app.persona.models import stage_for_score
from app.storage.postgres import SessionSnapshot

logger = logging.getLogger(__name__)


async def persist_node(state: AgentState, ctx: GraphContext) -> dict:
    return await _persist_job(state, ctx)


def _score_delta(state: AgentState) -> float:
    """关系分数微调启发式：被拦截扣分；情绪好加分，情绪差减分。"""
    if state.reject_reason:
        return -2.0
    delta = 0.5
    if state.mood.current_mood in ("happy", "excited", "shy") and state.mood.mood_intensity >= 0.6:
        delta += 0.5
    if state.mood.current_mood in ("angry", "sad") and state.mood.mood_intensity >= 0.7:
        delta -= 0.5
    return delta


async def _persist_job(state: AgentState, ctx: GraphContext) -> dict:
    claim_key: str | None = None
    if state.client_msg_id:
        # 幂等抢占：同一消息只允许落库一次（重试/重复请求在此短路）
        claim_key = f"idem:{state.session_id}:{state.client_msg_id}:written"
        if not await ctx.cache.set_nx(claim_key, "1", ttl=ctx.settings.idempotency_ttl_sec):
            logger.info(
                "[%s] duplicate persist skipped (client_msg_id=%s)",
                state.session_id,
                state.client_msg_id,
            )
            return {}

    try:
        # 1. 聊天记录
        if state.user_input.strip():
            await ctx.repo.append_chat(state.session_id, ChatMessage(role="user", content=state.user_input))
        if state.agent_reply:
            await ctx.repo.append_chat(state.session_id, ChatMessage(role="assistant", content=state.agent_reply))

        # 2. 新记忆 -> 向量库 + 关系库（按内容去重）
        existing = {m.content for m in await ctx.repo.list_memories(state.session_id)}
        fresh = [m for m in state.new_memories if m.content not in existing]
        if fresh:
            embeddings = await ctx.embedder.embed([m.content for m in fresh])
            for mem, emb in zip(fresh, embeddings):
                await ctx.vector_store.upsert(state.session_id, mem, emb)
                await ctx.repo.save_memory(state.session_id, mem)
            logger.info("[%s] persisted %d new memories", state.session_id, len(fresh))

        # 3. 关系分数推进 + 阶段跃迁
        new_score = max(0.0, min(100.0, state.relationship_score + _score_delta(state)))
        new_stage = stage_for_score(new_score)
        snap = SessionSnapshot(
            session_id=state.session_id,
            user_id=state.user_id,
            persona=state.persona,
            relationship_stage=new_stage,
            relationship_score=round(new_score, 2),
            mood=state.mood,
        )
        await ctx.repo.save_session(snap)

        # 4. Redis 会话缓存（短期会话状态，加速下次加载）
        cache_value = json.dumps(
            {
                "user_id": state.user_id,
                "persona": state.persona.model_dump(mode="json"),
                "relationship_stage": new_stage.value,
                "relationship_score": round(new_score, 2),
                "mood": state.mood.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        await ctx.cache.set(f"session:{state.session_id}:snapshot", cache_value, ttl=ctx.settings.session_ttl_sec)

        return {"relationship_score": round(new_score, 2), "relationship_stage": new_stage}
    except Exception as exc:  # noqa: BLE001
        if claim_key is not None:
            # 落库失败释放抢占标记，允许重试重新落库
            await ctx.cache.delete(claim_key)
        logger.exception("[%s] persist failed: %s", state.session_id, exc)
        return {}
