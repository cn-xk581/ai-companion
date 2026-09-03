"""会话列表 / 历史 / 详情（session/list、session/history、session/detail 的数据层）。"""
from __future__ import annotations

import pytest

from app.persona.models import Persona
from app.services.session_manager import (
    SessionManager,
    SessionNotFoundError,
    SessionOwnershipError,
)
from tests.conftest import make_ctx


async def test_list_sessions_scoped_to_user():
    """会话列表按用户隔离：只能看到自己的会话。"""
    ctx, _ = make_ctx()
    manager = SessionManager(ctx)

    await manager.chat("s1", "你好呀", "u1")
    await manager.chat("s2", "在吗", "u1")
    await manager.chat("s3", "你好", "u2")

    ids = sorted(s.session_id for s in await manager.list_sessions("u1"))
    assert ids == ["s1", "s2"]
    ids_u2 = sorted(s.session_id for s in await manager.list_sessions("u2"))
    assert ids_u2 == ["s3"]


async def test_session_history_and_snapshot_detail():
    """历史消息可读、详情含完整人设；越权均被拒绝。"""
    ctx, _ = make_ctx()
    manager = SessionManager(ctx)

    await manager.chat("s1", "我今天加班好累", "u1")

    history = await manager.get_history("s1", "u1")
    assert history, "聊天后应有历史消息"
    assert any(m.role == "user" and m.content == "我今天加班好累" for m in history)
    assert history[-1].role == "assistant"

    snap = await manager.get_snapshot("s1", "u1")
    assert snap.user_id == "u1"
    assert snap.persona.name == "小柔"
    assert snap.session_id == "s1"

    # 越权
    with pytest.raises(SessionOwnershipError):
        await manager.get_history("s1", "u2")
    with pytest.raises(SessionOwnershipError):
        await manager.get_snapshot("s1", "u2")


async def test_list_sessions_carries_persona_and_mood():
    """列表项应携带人设昵称、关系阶段与情绪，供前端会话列表渲染。"""
    ctx, _ = make_ctx()
    manager = SessionManager(ctx)

    await manager.chat("s1", "你好", "u1")
    sessions = await manager.list_sessions("u1")
    assert len(sessions) == 1
    s = sessions[0]
    assert s.persona.name == "小柔"
    assert s.relationship_stage.value in ("stranger", "acquaintance", "crush")
    assert s.mood.current_mood in ("happy", "neutral", "sad", "angry", "shy", "excited", "tired")


async def test_read_ops_do_not_auto_create_session():
    """读/管理类操作遇到不存在的 session 应 404，而非悄悄创建垃圾会话。"""
    ctx, _ = make_ctx()
    manager = SessionManager(ctx)

    with pytest.raises(SessionNotFoundError):
        await manager.get_history("ghost", "u1")
    with pytest.raises(SessionNotFoundError):
        await manager.get_snapshot("ghost", "u1")
    with pytest.raises(SessionNotFoundError):
        await manager.list_memories("ghost", "u1")
    with pytest.raises(SessionNotFoundError):
        await manager.reset_session("ghost", "u1")
    with pytest.raises(SessionNotFoundError):
        await manager.update_persona("ghost", Persona(), "u1")

    assert await manager.list_sessions("u1") == []
    assert await manager.list_sessions("u2") == []
