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
from app.services.session_manager import SessionManager, SessionOwnershipError
from app.storage.postgres import DuplicateUserError, InMemoryRepository
from tests.conftest import make_ctx


def test_password_hash_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    secret = "x" * 64
    token = create_access_token("u1", "alice", secret)
    payload = decode_access_token(token, secret)
    assert payload["sub"] == "u1"
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
    """session 强绑定用户：越权访问必须被拒绝。"""
    ctx, _ = make_ctx()
    manager = SessionManager(ctx)

    # u1 创建 session s1
    await manager.chat("s1", "你好呀", "u1")

    # u2 尝试访问 s1 -> 拒绝
    with pytest.raises(SessionOwnershipError):
        await manager.chat("s1", "在吗", "u2")
    with pytest.raises(SessionOwnershipError):
        await manager.list_memories("s1", "u2")
    with pytest.raises(SessionOwnershipError):
        await manager.reset_session("s1", "u2")

    # 本人可正常访问
    await manager.chat("s1", "再来一句", "u1")
    assert await manager.list_memories("s1", "u1") is not None
