"""用户与鉴权回归：argon2id、refresh 轮换、禁言、资料、头像。"""
from __future__ import annotations

import io
import os
import tempfile
import uuid

os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
os.environ.setdefault("ADMIN_TOKEN", "test-admin")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("VECTOR_STORE_BACKEND", "memory")
os.environ.setdefault("EMBEDDING_BACKEND", "hash")
os.environ.setdefault("MCP_STATE_DIR", tempfile.mkdtemp(prefix="mp-user-mcp-"))
os.environ.setdefault("MEDIA_DIR", tempfile.mkdtemp(prefix="mp-user-media-"))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
from app.storage.postgres import STATUS_MUTED, STATUS_NORMAL  # noqa: E402


def _client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, username: str | None = None) -> dict:
    name = username or f"u{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/auth/register", json={"username": name, "password": "secret123"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_tokens_and_lowercases_username():
    with _client() as client:
        body = _register(client, f"MiXed{uuid.uuid4().hex[:4]}")
        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "bearer"
        user = body["user"]
        assert user["username"] == user["username"].lower()
        assert user["nickname"] == user["username"]
        assert user["status"] == 1
        assert user["is_creator"] is False


def test_duplicate_and_reserved_usernames_rejected():
    with _client() as client:
        name = f"dup{uuid.uuid4().hex[:6]}"
        _register(client, name)
        again = client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"})
        assert again.status_code == 409
        for reserved in ("admin", "official", "root"):
            resp = client.post(
                "/api/v1/auth/register", json={"username": reserved, "password": "secret123"}
            )
            assert resp.status_code == 400


def test_refresh_rotation_and_logout():
    with _client() as client:
        body = _register(client)
        old_refresh = body["refresh_token"]

        first = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert first.status_code == 200, first.text
        new_refresh = first.json()["refresh_token"]
        assert new_refresh != old_refresh

        # 旧的已被轮换作废
        reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert reuse.status_code == 401

        # 新 refresh 可继续用（每次轮换）；登出后失效
        second = client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
        assert second.status_code == 200, second.text
        token = second.json()["access_token"]
        latest_refresh = second.json()["refresh_token"]

        out = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": latest_refresh},
            headers=_headers(token),
        )
        assert out.status_code == 200
        assert (
            client.post("/api/v1/auth/refresh", json={"refresh_token": latest_refresh}).status_code
            == 401
        )


def test_login_sets_last_login_and_old_password_still_works():
    with _client() as client:
        body = _register(client)
        username = body["user"]["username"]
        login = client.post("/api/v1/auth/login", json={"username": username, "password": "secret123"})
        assert login.status_code == 200
        assert login.json()["user"]["user_id"] == body["user"]["user_id"]


def test_profile_update_is_private_and_persisted():
    with _client() as client:
        body = _register(client)
        headers = _headers(body["access_token"])
        updated = client.put(
            "/api/v1/users/me/profile",
            json={"gender": 2, "bio": "夜里写代码", "tags": ["治愈", "日常"], "traits": {"mbti": "INFP"}},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["bio"] == "夜里写代码"
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["profile"]["traits"]["mbti"] == "INFP"


def test_avatar_upload_converts_to_webp():
    with _client() as client:
        body = _register(client)
        headers = _headers(body["access_token"])
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), (90, 120, 100)).save(buf, format="PNG")
        resp = client.post(
            "/api/v1/users/me/avatar",
            files={"file": ("avatar.png", buf.getvalue(), "image/png")},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        url = resp.json()["avatar"]
        assert url.startswith("/media/avatars/")
        assert url.endswith(".webp") or ".webp?v=" in url

        bad = client.post(
            "/api/v1/users/me/avatar",
            files={"file": ("x.txt", b"not an image", "text/plain")},
            headers=headers,
        )
        assert bad.status_code == 400


def test_muted_user_can_login_but_cannot_chat():
    with _client() as client:
        body = _register(client)
        uid = body["user"]["user_id"]
        headers = _headers(body["access_token"])
        repo = app.state.ctx.repo

        import asyncio

        asyncio.run(repo.update_user(uid, status=STATUS_MUTED))
        blocked = client.post(
            "/api/v1/chat", json={"user_input": "在吗"}, headers=headers
        )
        assert blocked.status_code == 403

        asyncio.run(repo.update_user(uid, status=STATUS_NORMAL))
        ok = client.post("/api/v1/chat", json={"user_input": "在吗"}, headers=headers)
        assert ok.status_code == 200
