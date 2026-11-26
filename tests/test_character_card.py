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
def test_edit_creates_new_version_and_session_can_sync():
    with _client() as client:
        body = _register(client)
        headers = _headers(body["access_token"])
        imported = client.post(
            "/api/v1/characters/import",
            files={"file": ("card.json", FIXTURE.read_bytes(), "application/json")},
            headers=headers,
        )
        cid = imported.json()["character"]["character_id"]
        sid = client.post(f"/api/v1/characters/{cid}/sessions", headers=headers).json()["session_id"]

        detail = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
        assert detail["character"]["version_no"] == 1
        assert detail["character"]["can_sync"] is False

        created = client.post(
            f"/api/v1/characters/{cid}/versions",
            json={
                "persona": {"personality": "新版性格：更直接"},
                "greeting": "新版开场白",
                "change_note": "调整性格与开场",
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["version"]["version_no"] == 2

        detail = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
        assert detail["character"]["version_no"] == 1
        assert detail["character"]["latest_version_no"] == 2
        assert detail["character"]["can_sync"] is True

        synced = client.post(f"/api/v1/sessions/{sid}/sync-character", headers=headers)
        assert synced.status_code == 200
        detail = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
        assert detail["character"]["version_no"] == 2
        assert detail["character"]["can_sync"] is False
        assert detail["persona"]["personality"] == "新版性格：更直接"

        exported = client.get(f"/api/v1/characters/{cid}/export.json", headers=headers).json()
        assert exported["data"]["first_mes"] == "新版开场白"

def test_search_by_name_and_tag_and_start_chat():
    with _client() as client:
        owner = _register(client)
        searcher = _register(client)
        imported = client.post(
            "/api/v1/characters/import",
            files={"file": ("card.json", FIXTURE.read_bytes(), "application/json")},
            headers=_headers(owner["access_token"]),
        )
        cid = imported.json()["character"]["character_id"]

        by_name = client.get(
            "/api/v1/characters/search", params={"q": "夷光"}, headers=_headers(searcher["access_token"])
        )
        assert by_name.status_code == 200
        found = by_name.json()["characters"]
        assert any(c["character_id"] == cid for c in found)
        hit = next(c for c in found if c["character_id"] == cid)
        assert hit["is_mine"] is False

        by_tag = client.get(
            "/api/v1/characters/search", params={"q": "山海拾遗"}, headers=_headers(searcher["access_token"])
        )
        assert any(c["character_id"] == cid for c in by_tag.json()["characters"])

        empty = client.get(
            "/api/v1/characters/search", params={"q": "不存在的角色xyz"}, headers=_headers(searcher["access_token"])
        )
        assert empty.json()["characters"] == []

        # 搜到就能开新对话（不必是自己创作）
        started = client.post(
            f"/api/v1/characters/{cid}/sessions", headers=_headers(searcher["access_token"])
        )
        assert started.status_code == 200, started.text
        detail = client.get(
            f"/api/v1/sessions/{started.json()['session_id']}", headers=_headers(searcher["access_token"])
        ).json()
        assert detail["persona"]["name"].startswith("夷光")

        # 但不能导出别人的人设
        denied = client.get(
            f"/api/v1/characters/{cid}/export.json", headers=_headers(searcher["access_token"])
        )
        assert denied.status_code == 403

def test_new_extraction_schema_maps_to_facts_and_events():
    from app.graph.nodes.wrapup import _memory_items

    facts, episodes = _memory_items(
        {
            "facts": [
                {"category": "preference", "key": "food.spicy", "value": "现在戒辣了", "confidence": 0.9}
            ],
            "events": [{"summary": "一起淋雨", "emotion": "touching", "importance": 6}],
            "affinity_delta": 2,
        },
        3,
    )
    assert facts[0]["key"] == "food.spicy"
    assert facts[0]["value"] == "现在戒辣了"
    assert episodes[0]["importance"] == 6
    assert episodes[0]["emotion_tag"] == "touching"


def test_reset_modes_clear_session_then_relation():
    import asyncio

    with _client() as client:
        body = _register(client)
        headers = _headers(body["access_token"])
        uid = body["user"]["user_id"]
        imported = client.post(
            "/api/v1/characters/import",
            files={"file": ("card.json", FIXTURE.read_bytes(), "application/json")},
            headers=headers,
        )
        cid = imported.json()["character"]["character_id"]
        sid = client.post(f"/api/v1/characters/{cid}/sessions", headers=headers).json()["session_id"]

        store = app.state.relations

        async def seed():
            relation = await store.ensure_relation(uid, cid)
            await store.add_fact(
                relation_id=relation.id,
                category="profile",
                fact_key="birthday",
                fact_value="3 月 2 日",
            )
            await store.add_episode(
                relation_id=relation.id,
                session_id=sid,
                kind="event",
                summary="一起淋雨",
                importance=5,
            )

        asyncio.run(seed())

        cleared = client.post(
            f"/api/v1/sessions/{sid}/reset", json={"mode": "session"}, headers=headers
        )
        assert cleared.status_code == 200, cleared.text
        memory = client.get(f"/api/v1/sessions/{sid}/memory", headers=headers).json()
        assert memory["facts"], "本会话重置不应清事实档案"
        assert not memory["episodes"], "本会话重置应清掉这段对话的情景"

        cleared = client.post(
            f"/api/v1/sessions/{sid}/reset", json={"mode": "all"}, headers=headers
        )
        assert cleared.status_code == 200
        memory = client.get(f"/api/v1/sessions/{sid}/memory", headers=headers).json()
        assert memory["facts"] == [] and memory["episodes"] == []
