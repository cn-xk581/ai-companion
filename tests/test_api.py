"""HTTP 契约：注册 → 聊天 → 会话/记忆/追踪 → 人设 → 运维开关。"""
from __future__ import annotations

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
os.environ.setdefault("MCP_STATE_DIR", tempfile.mkdtemp(prefix="mp-api-mcp-"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _client() -> TestClient:
    return TestClient(app)


def _auth(client: TestClient) -> dict[str, str]:
    username = f"api_{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/v1/auth/register", json={"username": username, "password": "secret123"})
    assert resp.status_code in (200, 201), resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_health_and_metadata():
    with _client() as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert body["domain"] == "companion"
        assert body["skills"]["total"] >= 8
        assert "calendar" in body["mcp"]["adapters"]

        headers = _auth(client)
        for path in ("/api/v1/skills", "/api/v1/agents", "/api/v1/mcp", "/api/v1/graph"):
            assert client.get(path, headers=headers).status_code == 200, path


def test_chat_then_sessions_history_and_trace():
    with _client() as client:
        headers = _auth(client)
        created = client.post("/api/v1/sessions", json={}, headers=headers).json()
        session_id = created["session_id"]

        chat = client.post(
            "/api/v1/chat",
            json={"session_id": session_id, "user_input": "在吗？今天有点想你", "client_msg_id": "m1"},
            headers=headers,
        )
        assert chat.status_code == 200, chat.text
        payload = chat.json()
        assert payload["status"] in ("done", "awaiting_review")
        assert payload["reply"]

        sessions = client.get("/api/v1/sessions", headers=headers).json()["sessions"]
        assert any(s["session_id"] == session_id for s in sessions)

        history = client.get(f"/api/v1/sessions/{session_id}/history", headers=headers).json()
        assert [m["role"] for m in history["messages"]][:2] == ["user", "assistant"]

        trace = client.get(f"/api/v1/turns/{payload['turn_id']}/trace", headers=headers)
        assert trace.status_code == 200
        assert any(e.get("kind") == "llm" for e in trace.json()["events"])


def test_persona_update_takes_effect():
    with _client() as client:
        headers = _auth(client)
        session = client.post("/api/v1/sessions", json={}, headers=headers).json()
        updated = client.put(
            f"/api/v1/sessions/{session['session_id']}/persona",
            json={
                "name": "阿雪",
                "age": 25,
                "personality": "冷静、话少",
                "speaking_style": "短句",
                "background": "图书管理员",
                "likes": ["雨"],
                "dislikes": ["吵"],
                "boundaries": ["不聊政治"],
            },
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["persona"]["name"] == "阿雪"


def test_admin_skill_switch_requires_token():
    with _client() as client:
        headers = _auth(client)
        assert client.post("/api/v1/skills/meme.reply/disable", headers=headers).status_code == 403
        ok = client.post(
            "/api/v1/skills/meme.reply/disable",
            headers={**headers, "X-Admin-Token": "test-admin"},
        )
        assert ok.status_code == 200
        assert ok.json()["enabled"] is False
        client.post(
            "/api/v1/skills/meme.reply/enable",
            headers={**headers, "X-Admin-Token": "test-admin"},
        )


def test_chat_requires_auth():
    with _client() as client:
        resp = client.post("/api/v1/chat", json={"user_input": "在吗"})
        assert resp.status_code in (401, 403)
