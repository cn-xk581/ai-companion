"""鉴权与归属单元/集成测试。"""
from __future__ import annotations

import jwt as pyjwt
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.storage.postgres import DuplicateUserError, InMemoryRepository


def test_password_hash_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert h.startswith("$argon2id$")
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    secret = "x" * 64
    token = create_access_token(1, "alice", secret)
    payload = decode_access_token(token, secret)
    assert payload["sub"] == "1"
    assert payload["username"] == "alice"

    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(token, "w" * 64)
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token("garbage", secret)


async def test_user_repo_register_duplicate():
    repo = InMemoryRepository()
    user = await repo.create_user("alice", hash_password("pw123456"))
    assert await repo.get_user_by_username("alice") is not None
    assert (await repo.get_user(user.id)).username == "alice"
    with pytest.raises(DuplicateUserError):
        await repo.create_user("alice", "other")


async def test_session_ownership_isolation():
    """会话强绑定用户：越权访问必须被拒绝。"""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graph.builder import build_graph
    from app.services.chat_service import ChatService, SessionOwnershipError
    from tests.conftest import make_ctx

    ctx = await make_ctx()
    graph = build_graph(ctx, InMemorySaver())
    service = ChatService(ctx, graph, {"backend": "memory"})

    session = await service.create_session("s1", 1)
    await service.chat(session_id=session.session_id, user_id=1, user_input="你好呀")

    with pytest.raises(SessionOwnershipError):
        await service.require_session("s1", 2)
    with pytest.raises(SessionOwnershipError):
        await service.create_session("s1", 2)
    with pytest.raises(SessionOwnershipError):
        await service.list_memories(2, session_id="s1")

    # 本人可正常访问
    record = await service.require_session("s1", 1)
    assert record.user_id == 1
