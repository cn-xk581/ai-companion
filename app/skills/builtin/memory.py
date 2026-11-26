"""Skill: memory.recall / memory.remember —— 跨会话长期记忆。

- 记忆只沉淀「关于你的事实」，不参与关系/情绪判定；
- 召回用向量检索（scope = user_id），写入前去重；
- 向量库/embedder 未配置时降级为空召回 / 跳过写入，不阻断对话。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.memory import MemoryItem
from app.skills.base import Skill, SkillContext


class RecallInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=5, ge=1, le=10)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


class RecallHit(BaseModel):
    content: str
    score: float
    importance: float


class RecallOutput(BaseModel):
    memories: list[str] = Field(default_factory=list)
    hits: list[RecallHit] = Field(default_factory=list)
    degraded: bool = False


class RememberItem(BaseModel):
    content: str = Field(min_length=2, max_length=200)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    category: str = Field(default="fact", pattern="^(fact|preference|event|anniversary|story)$")
    tags: list[str] = Field(default_factory=list)


class RememberInput(BaseModel):
    items: list[RememberItem] = Field(min_length=1, max_length=5)


class RememberOutput(BaseModel):
    saved: int = 0
    skipped: int = 0
    ids: list[str] = Field(default_factory=list)
    degraded: bool = False


class MemoryRecallSkill(Skill):
    name = "memory.recall"
    version = "1.2.0"
    description = "按语义召回关于该用户的长期记忆"
    owner = "companion-memory"
    tags = ["business", "memory", "read"]
    Input = RecallInput
    Output = RecallOutput
    mcp_tools = []
    timeout_sec = 4.0
    cache_ttl = 30

    async def run(self, payload: RecallInput, sctx: SkillContext) -> RecallOutput:
        if sctx.embedder is None or sctx.vector_store is None:
            return RecallOutput(degraded=True)
        embeddings = await sctx.embedder.embed([payload.query], is_query=True)
        hits = await sctx.vector_store.search(
            str(sctx.user_id), embeddings[0], top_k=payload.top_k, threshold=payload.min_score
        )
        rows = [
            RecallHit(content=mem.content, score=round(float(score), 3), importance=mem.importance)
            for mem, score in hits
        ]
        await sctx.tracer.decision("memory.recalled", query=payload.query[:60], hits=len(rows))
        return RecallOutput(memories=[r.content for r in rows], hits=rows)


class MemoryRememberSkill(Skill):
    name = "memory.remember"
    version = "1.2.0"
    description = "把本轮值得记住的事实写进长期记忆（按内容去重）"
    owner = "companion-memory"
    tags = ["business", "memory", "write"]
    Input = RememberInput
    Output = RememberOutput
    mcp_tools = []
    timeout_sec = 4.0
    idempotent = False

    async def run(self, payload: RememberInput, sctx: SkillContext) -> RememberOutput:
        if sctx.repo is None or sctx.embedder is None or sctx.vector_store is None:
            return RememberOutput(degraded=True, skipped=len(payload.items))

        existing = {m.content for m in await sctx.repo.list_memories(sctx.user_id)}
        fresh = [item for item in payload.items if item.content not in existing]
        skipped = len(payload.items) - len(fresh)
        if not fresh:
            return RememberOutput(saved=0, skipped=skipped)

        embeddings = await sctx.embedder.embed([item.content for item in fresh])
        ids: list[str] = []
        for item, embedding in zip(fresh, embeddings):
            memory = MemoryItem(
                content=item.content,
                importance=item.importance,
                category=item.category,
                tags=item.tags,
            )
            await sctx.repo.save_memory(sctx.user_id, memory)
            await sctx.vector_store.upsert(str(sctx.user_id), memory, embedding)
            ids.append(memory.id)
        await sctx.tracer.decision("memory.remembered", saved=len(ids), skipped=skipped)
        return RememberOutput(saved=len(ids), skipped=skipped, ids=ids)
