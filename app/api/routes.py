"""FastAPI 路由：接入层（全部端点 JWT 鉴权 + session 归属校验）。

- POST /auth/register       注册（见 app.auth.routes）
- POST /auth/login          登录
- POST /chat                聊天（限流 + 会话锁 + 可靠持久化）
- POST /chat/stream         聊天（SSE 流式）
- GET  /session/list        我的会话列表（供前端会话列表）
- GET  /session/detail      会话详情（完整人设/关系/情绪）
- GET  /session/history     会话聊天历史
- POST /session/reset       一键重置记忆/关系状态（仅本人）
- GET  /memory/list         查看记忆（仅本人）
- POST /memory/delete       删除记忆（仅本人）
- PUT  /persona/update      动态修改人设（仅本人）
- GET  /health              健康检查（公开）
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    MemoryDeleteRequest,
    MemoryDeleteResponse,
    MemoryListResponse,
    PersonaUpdateRequest,
    PersonaUpdateResponse,
    SessionDetailResponse,
    SessionListItem,
    SessionListResponse,
    SessionResetRequest,
    SessionResetResponse,
)
from app.auth.deps import AuthUser, get_current_user
from app.graph.context import GraphContext
from app.services.session_manager import (
    SessionManager,
    SessionNotFoundError,
    SessionOwnershipError,
)

router = APIRouter(prefix="/api/v1", tags=["aigf"])


def _deps(request: Request) -> tuple[GraphContext, SessionManager]:
    return request.app.state.ctx, request.app.state.manager


async def _enforce_limits(ctx: GraphContext, user_id: str, session_id: str) -> None:
    """限流 + 会话锁（与 /chat 和 /chat/stream 共用）。"""
    count = await ctx.cache.incr(f"rl:{user_id}", ttl=60)
    if count > ctx.settings.chat_rate_limit_per_min:
        raise HTTPException(status_code=429, detail="太频繁啦，歇一会儿再聊～")


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    ctx, manager = _deps(request)
    session_id = req.session_id or uuid.uuid4().hex
    await _enforce_limits(ctx, user.user_id, session_id)

    async with ctx.cache.lock(
        f"lock:{session_id}",
        timeout=ctx.settings.lock_acquire_timeout_sec,
        ttl=ctx.settings.lock_ttl_sec,
    ) as acquired:
        if not acquired:
            raise HTTPException(status_code=429, detail="上一条消息还在处理中，稍等一下～")
        try:
            result = await manager.chat(session_id, req.user_input, user.user_id, req.client_msg_id)
        except SessionOwnershipError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    return ChatResponse(**result)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    ctx, manager = _deps(request)
    session_id = req.session_id or uuid.uuid4().hex
    await _enforce_limits(ctx, user.user_id, session_id)

    async def event_stream():
        # 会话锁覆盖整个流式过程
        async with ctx.cache.lock(
            f"lock:{session_id}",
            timeout=ctx.settings.lock_acquire_timeout_sec,
            ttl=ctx.settings.lock_ttl_sec,
        ) as acquired:
            if not acquired:
                yield f"data: {json.dumps({'type': 'error', 'message': '上一条消息还在处理中'}, ensure_ascii=False)}\n\n"
                return

            # 幂等重放：同一消息的重试直接返回上次结果，不再重跑图（避免重复落库/重复计费）
            cached = await manager.get_cached_result(session_id, req.client_msg_id)
            if cached is not None:
                yield f"data: {json.dumps({'type': 'done', **cached}, ensure_ascii=False)}\n\n"
                return

            try:
                state = await manager.build_state(session_id, req.user_input, user.user_id, req.client_msg_id)
            except SessionOwnershipError as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
                return

            final_state = state
            try:
                async for mode, chunk in manager.graph.astream(state, stream_mode=["custom", "values"]):
                    if mode == "custom":
                        for item in _iter_custom(chunk):
                            if isinstance(item, dict) and item.get("type") == "token":
                                yield f"data: {json.dumps({'type': 'token', 'content': item['content']}, ensure_ascii=False)}\n\n"
                    elif mode == "values":
                        final_state = chunk
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
                return

            result = manager.format_result(final_state, session_id)
            if result.get("reply"):
                await manager.store_result(session_id, req.client_msg_id, result)
            yield f"data: {json.dumps({'type': 'done', **result}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _iter_custom(chunk):
    """兼容 langgraph 自定义流可能返回 list 或单条的情况。"""
    if isinstance(chunk, (list, tuple)):
        return chunk
    return [chunk]


@router.get("/session/list", response_model=SessionListResponse)
async def session_list(request: Request, user: AuthUser = Depends(get_current_user)):
    ctx, manager = _deps(request)
    snaps = await manager.list_sessions(user.user_id)
    items: list[SessionListItem] = []
    for snap in snaps:
        recent = await ctx.repo.get_recent_chat(snap.session_id, 1)
        last_msg = recent[0].content if recent else None
        last_time = recent[0].timestamp if recent else None
        items.append(
            SessionListItem(
                session_id=snap.session_id,
                name=snap.persona.name,
                avatar=snap.persona.avatar,
                relationship_stage=snap.relationship_stage.value,
                relationship_score=snap.relationship_score,
                mood=snap.mood.current_mood,
                mood_intensity=snap.mood.mood_intensity,
                last_message=last_msg,
                last_time=last_time,
            )
        )
    # 有最后消息的按时间倒序（最新在上），无消息的排后面
    _EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items.sort(
        key=lambda x: (x.last_time is not None, x.last_time or _EPOCH),
        reverse=True,
    )
    return SessionListResponse(sessions=items)


@router.get("/session/detail", response_model=SessionDetailResponse)
async def session_detail(session_id: str, request: Request, user: AuthUser = Depends(get_current_user)):
    _, manager = _deps(request)
    try:
        snap = await manager.get_snapshot(session_id, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return SessionDetailResponse(
        session_id=snap.session_id,
        persona=snap.persona,
        relationship_stage=snap.relationship_stage.value,
        relationship_score=snap.relationship_score,
        mood=snap.mood,
    )


@router.get("/session/history", response_model=ChatHistoryResponse)
async def session_history(
    session_id: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
    limit: int = 100,
):
    _, manager = _deps(request)
    try:
        messages = await manager.get_history(session_id, user.user_id, limit)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ChatHistoryResponse(session_id=session_id, messages=messages)


@router.post("/session/reset", response_model=SessionResetResponse)
async def session_reset(req: SessionResetRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    _, manager = _deps(request)
    try:
        await manager.reset_session(req.session_id, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return SessionResetResponse(session_id=req.session_id)


@router.get("/memory/list", response_model=MemoryListResponse)
async def memory_list(session_id: str, request: Request, user: AuthUser = Depends(get_current_user)):
    _, manager = _deps(request)
    try:
        memories = await manager.list_memories(session_id, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return MemoryListResponse(session_id=session_id, memories=memories)


@router.post("/memory/delete", response_model=MemoryDeleteResponse)
async def memory_delete(req: MemoryDeleteRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    _, manager = _deps(request)
    try:
        deleted = await manager.delete_memory(req.session_id, req.memory_id, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return MemoryDeleteResponse(deleted=deleted)


@router.put("/persona/update", response_model=PersonaUpdateResponse)
async def persona_update(req: PersonaUpdateRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    _, manager = _deps(request)
    try:
        snap = await manager.update_persona(req.session_id, req.persona, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return PersonaUpdateResponse(session_id=req.session_id, persona=snap.persona)


@router.get("/health")
async def health(request: Request):
    ctx, _ = _deps(request)
    return {
        "status": "ok",
        "app": ctx.settings.app_name,
        "environment": ctx.settings.environment,
        "vector_store_backend": ctx.settings.vector_store_backend,
    }
