"""会话记忆：查看 / 置顶 / 修改 / 归档（按羁绊聚合）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthUser, get_current_user

router = APIRouter(prefix="/api/v1", tags=["memory"])


class FactUpdate(BaseModel):
    value: str | None = Field(default=None, max_length=255)
    pinned: bool | None = None


class EpisodeUpdate(BaseModel):
    pinned: bool | None = None


async def _owned_relation(request: Request, user: AuthUser, session_id: str):
    repo = request.app.state.ctx.repo
    session = await repo.get_session(session_id)
    if session is None or session.user_id != user.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if not session.character_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="该会话没有绑定角色")
    store = request.app.state.relations
    relation = await store.get_by_user_character(user.user_id, session.character_id)
    if relation is None:
        relation = await store.ensure_relation(user.user_id, session.character_id)
    return relation


async def _owned_fact(request: Request, user: AuthUser, fact_id: int):
    store = request.app.state.relations
    fact = await store.get_fact(fact_id)
    if fact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记忆不存在")
    relation = await store.get_relation(fact.relation_id)
    if relation is None or relation.user_id != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权操作该记忆")
    return fact


async def _owned_episode(request: Request, user: AuthUser, episode_id: int):
    store = request.app.state.relations
    episode = await store.get_episode(episode_id)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记忆不存在")
    relation = await store.get_relation(episode.relation_id)
    if relation is None or relation.user_id != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权操作该记忆")
    return episode


@router.get("/sessions/{session_id}/memory")
async def session_memory(
    session_id: str,
    request: Request,
    q: str = "",
    user: AuthUser = Depends(get_current_user),
):
    relation = await _owned_relation(request, user, session_id)
    data = await request.app.state.memory.grouped(relation.id, session_id, q)
    return {
        "relation": {
            "id": relation.id,
            "character_id": relation.character_id,
            "affinity_score": relation.affinity_score,
            "relationship_stage": relation.relationship_stage,
            "current_mood": relation.current_mood,
            "user_nickname": relation.user_nickname,
            "total_turns": relation.total_turns,
            "last_interaction_at": relation.last_interaction_at.isoformat()
            if relation.last_interaction_at
            else None,
        },
        **data,
    }


class RelationUpdate(BaseModel):
    user_nickname: str | None = Field(default=None, max_length=32)
    user_persona_override: str | None = Field(default=None, max_length=500)


@router.patch("/sessions/{session_id}/relation")
async def update_relation(
    session_id: str,
    body: RelationUpdate,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    relation = await _owned_relation(request, user, session_id)
    fields = {}
    if body.user_nickname is not None:
        fields["user_nickname"] = body.user_nickname.strip()[:32]
    if body.user_persona_override is not None:
        fields["user_persona_override"] = body.user_persona_override.strip()[:500]
    if fields:
        await request.app.state.relations.update_relation(relation.id, **fields)
    return {"ok": True}


@router.patch("/memory/facts/{fact_id}")
async def update_fact(
    fact_id: int,
    body: FactUpdate,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    await _owned_fact(request, user, fact_id)
    fields = {}
    if body.value is not None and body.value.strip():
        fields["fact_value"] = body.value.strip()
    if body.pinned is not None:
        fields["is_pinned"] = body.pinned
    if fields:
        await request.app.state.relations.update_fact(fact_id, **fields)
    return {"ok": True}


@router.delete("/memory/facts/{fact_id}")
async def archive_fact(
    fact_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    await _owned_fact(request, user, fact_id)
    await request.app.state.relations.update_fact(fact_id, status=3)
    return {"ok": True}


@router.patch("/memory/episodes/{episode_id}")
async def update_episode(
    episode_id: int,
    body: EpisodeUpdate,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    await _owned_episode(request, user, episode_id)
    if body.pinned is not None:
        await request.app.state.relations.update_episode(episode_id, is_pinned=body.pinned)
    return {"ok": True}


@router.delete("/memory/episodes/{episode_id}")
async def archive_episode(
    episode_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    await _owned_episode(request, user, episode_id)
    await request.app.state.relations.update_episode(episode_id, status=3)
    return {"ok": True}
