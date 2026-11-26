"""角色卡解析 / 导入导出 / 会话绑定。"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
os.environ.setdefault("ADMIN_TOKEN", "test-admin")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("VECTOR_STORE_BACKEND", "memory")
os.environ.setdefault("EMBEDDING_BACKEND", "hash")
os.environ.setdefault("MCP_STATE_DIR", tempfile.mkdtemp(prefix="mp-char-mcp-"))
os.environ.setdefault("MEDIA_DIR", tempfile.mkdtemp(prefix="mp-char-media-"))

from fastapi.testclient import TestClient  # noqa: E402

from app.domain.character_card import (  # noqa: E402
    build_export_card,
    embed_card_into_png,
    extract_card_json_from_png,
    make_placeholder_png,
    normalize_card,
)
from app.main import app  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "character_v3.json"


def _card() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient) -> dict:
    name = f"c{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_normalize_v3_card_keeps_all_fields():
    parsed = normalize_card(_card())
    assert parsed.spec == "chara_card_v3"
    assert parsed.name.startswith("夷光")
    assert len(parsed.tags) == 6
    assert parsed.greeting.startswith("夷光把一枚")
    assert len(parsed.alternate_greetings) == 2
    assert parsed.lorebook is not None and len(parsed.lorebook["entries"]) == 15
    assert parsed.dialogue_samples, "mes_example 应解析出示例"
    assert parsed.persona["system_prompt"]
    assert parsed.persona["extensions"]["regex_scripts"]


def test_export_card_roundtrip_is_lossless():
    parsed = normalize_card(_card())
    exported = build_export_card(
        persona=parsed.persona,
        raw_card=parsed.raw,
        greeting=parsed.greeting,
        alternate_greetings=parsed.alternate_greetings,
        lorebook=parsed.lorebook,
    )
    again = normalize_card(exported)
    assert again.name == parsed.name
    assert again.persona["description"] == parsed.persona["description"]
    assert again.persona["system_prompt"] == parsed.persona["system_prompt"]
    assert len(again.lorebook["entries"]) == 15
    assert again.persona["extensions"]["regex_scripts"][0]["scriptName"] == "清理模型思维痕迹"


def test_png_embed_and_extract_roundtrip():
    card = _card()
    png = embed_card_into_png(make_placeholder_png(), card)
    assert png.startswith(b"\x89PNG")
    extracted, chunk = extract_card_json_from_png(png)
    assert chunk == "ccv3"
    assert extracted["data"]["name"] == card["data"]["name"]


def test_import_export_and_session_binding():
    with _client() as client:
        body = _register(client)
        headers = _headers(body["access_token"])

        imported = client.post(
            "/api/v1/characters/import",
            files={"file": ("card.json", FIXTURE.read_bytes(), "application/json")},
            headers=headers,
        )
        assert imported.status_code == 200, imported.text
        char = imported.json()["character"]
        assert char["name"].startswith("夷光")
        assert char["card_spec"] == "chara_card_v3"
        cid = char["character_id"]

        mine = client.get("/api/v1/characters/mine", headers=headers).json()["characters"]
        assert any(c["character_id"] == cid for c in mine)

        detail = client.get(f"/api/v1/characters/{cid}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["lorebook"]["entries"]

        exported = client.get(f"/api/v1/characters/{cid}/export.json", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["spec"] == "chara_card_v3"

        png = client.get(f"/api/v1/characters/{cid}/export.png", headers=headers)
        assert png.status_code == 200
        assert png.headers["content-type"].startswith("image/png")
        card, _ = extract_card_json_from_png(png.content)
        assert card["data"]["name"] == exported.json()["data"]["name"]

        # 会话绑定角色 + 开场白落库
        created = client.post(f"/api/v1/characters/{cid}/sessions", headers=headers)
        assert created.status_code == 200, created.text
        sid = created.json()["session_id"]
        session = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
        assert session["persona"]["name"].startswith("夷光")
        history = client.get(f"/api/v1/sessions/{sid}/history", headers=headers).json()["messages"]
        assert history and history[0]["role"] == "assistant"

        # 同步接口可用
        synced = client.post(f"/api/v1/sessions/{sid}/sync-character", headers=headers)
        assert synced.status_code == 200


def test_only_creator_can_export():
    with _client() as client:
        owner = _register(client)
        other = _register(client)
        imported = client.post(
            "/api/v1/characters/import",
            files={"file": ("card.json", FIXTURE.read_bytes(), "application/json")},
            headers=_headers(owner["access_token"]),
        )
        cid = imported.json()["character"]["character_id"]
        denied = client.get(
            f"/api/v1/characters/{cid}/export.json", headers=_headers(other["access_token"])
        )
        assert denied.status_code == 403
