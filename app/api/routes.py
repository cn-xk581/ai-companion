"""HTTP 接入层：对话（含 SSE）、会话与人设、记忆、轮次审计、能力运维。

只做鉴权/限流/参数绑定/错误码映射；编排在 ChatService，规则在 Skill。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    CharacterVersionInfo,
    AgentListResponse,
    ChatRequest,
    ChatResponse,
    GraphResponse,
    HealthResponse,
    HistoryMessage,
    HistoryResponse,
    MCPListResponse,
    MemoryDeleteResponse,
    MemoryItemOut,
    MemoryListResponse,
    ResetRequest,
    ResetResponse,
    ResumeRequest,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListItem,
    SessionListResponse,
    SkillActionResponse,
    SkillListResponse,
    SkillRegisterRequest,
    TraceResponse,
    TurnResponse,
)
from app.auth.deps import AuthUser, ensure_can_chat, get_current_user
from app.domain.companion import MOOD_LABELS, STAGE_LABELS, Persona
from app.graph.builder import topology
from app.graph.context import GraphContext
from app.services.chat_service import (
    ChatService,
    SessionNotFoundError,
    SessionOwnershipError,
    TurnNotFoundError,
)
from app.skills.models import SkillMeta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["companion"])


def _deps(request: Request) -> tuple[GraphContext, ChatService]:
    return request.app.state.ctx, request.app.state.chat


def require_admin(
    request: Request,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> None:
    expected = request.app.state.ctx.settings.admin_token
    if not expected:
        raise HTTPException(status_code=403, detail="运维接口未开启：请先配置 ADMIN_TOKEN")
    if x_admin_token != expected:
        raise HTTPException(status_code=403, detail="运维令牌无效")


async def _rate_limit(ctx: GraphContext, user_id: str) -> None:
    count = await ctx.cache.incr(f"rl:chat:{user_id}", ttl=60)
    if count > ctx.settings.chat_rate_limit_per_min:
        raise HTTPException(status_code=429, detail="说得有点快，让我喘口气～")


# ------------------------------ 对话 ------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    """一轮对话（同步返回）。`client_msg_id` 相同则幂等返回上次结果。"""
    ctx, service = _deps(request)
    ensure_can_chat(user)
    await _rate_limit(ctx, user.user_id)
    session_id = req.session_id or uuid.uuid4().hex
    try:
        await service.create_session(session_id, user.user_id)
        payload = await service.chat(
            session_id=session_id,
            user_id=user.user_id,
            user_input=req.user_input,
            client_msg_id=req.client_msg_id,
        )
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ChatResponse(**payload)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    """SSE 流式：meta → token* → done/error。`done` 里带最终文本与最新关系/情绪。"""
    ctx, service = _deps(request)
    ensure_can_chat(user)
    await _rate_limit(ctx, user.user_id)
    session_id = req.session_id or uuid.uuid4().hex
    try:
        await service.create_session(session_id, user.user_id)
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in service.stream(
                session_id=session_id,
                user_id=user.user_id,
                user_input=req.user_input,
                client_msg_id=req.client_msg_id,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001 - 流里不能抛 500
            logger.exception("[chat/stream] 流式失败")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ------------------------------ 会话 ------------------------------


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(request: Request, user: AuthUser = Depends(get_current_user)):
    _, service = _deps(request)
    rows = await service.list_sessions(user.user_id)
    return SessionListResponse(sessions=[SessionListItem(**row) for row in rows])


@router.post("/sessions", response_model=SessionDetailResponse)
async def create_session(
    req: SessionCreateRequest, request: Request, user: AuthUser = Depends(get_current_user)
):
    _, service = _deps(request)
    try:
        record = await service.create_session(req.session_id, user.user_id, greeting=req.greeting or "")
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _detail(record)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def session_detail(
    session_id: str, request: Request, user: AuthUser = Depends(get_current_user)
):
    _, service = _deps(request)
    try:
        record = await service.require_session(session_id, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _detail(record, await _character_info(request, record))


@router.post("/sessions/{session_id}/sync-character")
async def sync_session_character(
    session_id: str, request: Request, user: AuthUser = Depends(get_current_user)
):
    """把会话的人设同步到角色当前版本（显式操作，不自动跟随）。"""
    service = request.app.state.character_service
    try:
        return await service.sync_session(user.user_id, session_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/sessions/{session_id}/history", response_model=HistoryResponse)
async def session_history(
    session_id: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
    limit: int = 100,
):
    ctx, service = _deps(request)
    try:
        await service.require_session(session_id, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    messages = await ctx.repo.recent_messages(session_id, min(max(limit, 1), 300))
    return HistoryResponse(
        session_id=session_id,
        messages=[
            HistoryMessage(role=m.role, content=m.content, tool_name=m.tool_name, timestamp=m.timestamp)
            for m in messages
        ],
    )


@router.put("/sessions/{session_id}/persona", response_model=SessionDetailResponse)
async def update_persona(
    session_id: str,
    persona: Persona,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """改人设：下一轮立刻生效（人设是持久状态，不是提示词）。"""
    _, service = _deps(request)
    try:
        record = await service.update_persona(session_id, persona, user.user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _detail(record)


@router.post("/sessions/{session_id}/reset", response_model=ResetResponse)
async def reset_session(
    session_id: str,
    req: ResetRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    _, service = _deps(request)
    try:
        await service.reset_session(session_id, user.user_id, clear_memories=req.clear_memories, mode=req.mode)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ResetResponse(session_id=session_id, cleared_memories=req.clear_memories)


# ------------------------------ 记忆 ------------------------------


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(request: Request, user: AuthUser = Depends(get_current_user)):
    _, service = _deps(request)
    rows = await service.list_memories(user.user_id)
    return MemoryListResponse(user_id=user.user_id, memories=[MemoryItemOut(**r) for r in rows])


@router.delete("/memories/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory(
    memory_id: str, request: Request, user: AuthUser = Depends(get_current_user)
):
    _, service = _deps(request)
    return MemoryDeleteResponse(deleted=await service.delete_memory(user.user_id, memory_id))


# ------------------------------ 轮次（人工复核 / 审计） ------------------------------


@router.get("/turns/{turn_id}", response_model=TurnResponse)
async def get_turn(turn_id: str, request: Request, user: AuthUser = Depends(get_current_user)):
    _, service = _deps(request)
    try:
        return TurnResponse(**await service.get_turn(turn_id, user.user_id))
    except TurnNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.post("/turns/{turn_id}/resume", response_model=ChatResponse)
async def resume_turn(
    turn_id: str,
    req: ResumeRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """人工复核：approve 照常发出，reject 替换为安全话术并落库。"""
    _, service = _deps(request)
    try:
        payload = await service.resume(
            turn_id=turn_id,
            user_id=user.user_id,
            decision=req.decision,
            feedback=req.feedback,
            by=req.by,
        )
    except TurnNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ChatResponse(**payload)


@router.get("/turns/{turn_id}/trace", response_model=TraceResponse)
async def turn_trace(
    turn_id: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
    limit: int = 500,
):
    _, service = _deps(request)
    try:
        return TraceResponse(**await service.trace(turn_id, user.user_id, limit=min(limit, 2000)))
    except TurnNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


# ------------------------------ 能力与运维 ------------------------------


@router.get("/skills", response_model=SkillListResponse)
async def list_skills(request: Request, user: AuthUser = Depends(get_current_user)):
    ctx, _ = _deps(request)
    return SkillListResponse(skills=await ctx.skills.list_public(), stats=await ctx.skills.stats())


@router.get("/agents", response_model=AgentListResponse)
async def list_agents(request: Request, user: AuthUser = Depends(get_current_user)):
    from app.agents.registry import list_agents as _list

    return AgentListResponse(agents=[a.public() for a in _list()])


@router.get("/mcp", response_model=MCPListResponse)
async def list_mcp(request: Request, user: AuthUser = Depends(get_current_user)):
    ctx, _ = _deps(request)
    return MCPListResponse(tools=ctx.mcp_registry.describe(), health=await ctx.mcp.health())


@router.get("/graph", response_model=GraphResponse)
async def get_graph(user: AuthUser = Depends(get_current_user)):
    return GraphResponse(**topology())


@router.post(
    "/skills/{name}/enable", response_model=SkillActionResponse, dependencies=[Depends(require_admin)]
)
async def enable_skill(name: str, request: Request):
    ctx, _ = _deps(request)
    try:
        meta = await ctx.skills.set_enabled(name, True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Skill 不存在或无法上线：{exc}")
    return SkillActionResponse(skill=name, enabled=meta.enabled, meta=meta.public())


@router.post(
    "/skills/{name}/disable", response_model=SkillActionResponse, dependencies=[Depends(require_admin)]
)
async def disable_skill(name: str, request: Request):
    ctx, _ = _deps(request)
    try:
        meta = await ctx.skills.set_enabled(name, False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Skill 不存在或无法下线：{exc}")
    return SkillActionResponse(skill=name, enabled=meta.enabled, meta=meta.public())


@router.post(
    "/skills/register", response_model=SkillActionResponse, dependencies=[Depends(require_admin)]
)
async def register_skill(body: SkillRegisterRequest, request: Request):
    ctx, _ = _deps(request)
    try:
        meta = await ctx.skills.register(
            SkillMeta(
                name=body.name,
                class_path=body.class_path,
                description=body.description,
                owner=body.owner,
                enabled=body.enabled,
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"注册失败：{exc}")
    return SkillActionResponse(skill=meta.name, registered=True, meta=meta.public())


@router.delete(
    "/skills/{name}", response_model=SkillActionResponse, dependencies=[Depends(require_admin)]
)
async def unregister_skill(name: str, request: Request):
    ctx, _ = _deps(request)
    if not await ctx.skills.unregister(name):
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return SkillActionResponse(skill=name, deleted=True)


# ------------------------------ 健康检查 ------------------------------


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    ctx, _ = _deps(request)
    base = ctx.settings.llm_base_url or ""
    try:
        host = urlparse(base).hostname or ""
    except ValueError:
        host = ""
    return HealthResponse(
        status="ok",
        app=ctx.settings.app_name,
        environment=ctx.settings.environment,
        domain=ctx.settings.domain,
        llm={
            "model": ctx.settings.llm_model,
            "host": host,
            "configured": bool(ctx.settings.llm_api_key),
            "image_model": ctx.settings.image_model or "",
        },
        checkpoint=ctx.checkpointer_desc,
        skills=await ctx.skills.stats(),
        mcp=await ctx.mcp.health(),
        singleflight={"enabled": ctx.singleflight._enabled, "stats": ctx.singleflight.stats},
    )


async def _character_info(request, record) -> CharacterVersionInfo | None:
    if not record.character_id:
        return None
    store = request.app.state.characters
    char = await store.get(record.character_id)
    if char is None:
        return None
    latest = await store.get_current_version(char.id)
    current = (
        await store.get_version(record.character_version_id)
        if record.character_version_id
        else None
    )
    return CharacterVersionInfo(
        character_id=char.id,
        version_id=current.id if current else None,
        version_no=current.version_no if current else None,
        latest_version_id=latest.id if latest else None,
        latest_version_no=latest.version_no if latest else None,
        can_sync=bool(latest and latest.id != (current.id if current else None)),
        name=char.name,
    )


def _detail(record, character: CharacterVersionInfo | None = None) -> SessionDetailResponse:
    return SessionDetailResponse(
        session_id=record.session_id,
        persona=record.persona,
        stage=record.stage.value,
        stage_label=STAGE_LABELS.get(record.stage.value, record.stage.value),
        score=round(record.score, 1),
        mood=record.mood,
        mood_label=MOOD_LABELS.get(record.mood.current_mood, record.mood.current_mood),
        character=character,
    )


def service_info(settings) -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "domain": settings.domain,
        "version": "2.1.0",
        "docs": "/docs",
        "api": {
            "chat": "POST /api/v1/chat",
            "stream": "POST /api/v1/chat/stream",
            "sessions": "GET /api/v1/sessions",
            "persona": "PUT /api/v1/sessions/{id}/persona",
            "history": "GET /api/v1/sessions/{id}/history",
            "memories": "GET /api/v1/memories",
            "turn": "GET /api/v1/turns/{turn_id}",
            "trace": "GET /api/v1/turns/{turn_id}/trace",
            "resume": "POST /api/v1/turns/{turn_id}/resume",
            "skills": "GET /api/v1/skills",
            "health": "GET /api/v1/health",
        },
        "auth": "先 POST /api/v1/auth/register 或 /auth/login 拿 JWT，再带 Authorization: Bearer <token>",
    }
