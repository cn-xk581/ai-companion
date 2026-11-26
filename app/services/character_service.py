"""角色卡导入 / 导出 / 会话绑定。"""
from __future__ import annotations

import io
import json
import uuid
from typing import Any

from app.domain.character_card import (
    CardParseError,
    build_export_card,
    embed_card_into_png,
    extract_card_json_from_png,
    make_placeholder_png,
    normalize_card,
)
from app.domain.companion import ChatMessage, Persona
from app.storage.character_store import (
    STATUS_ONLINE,
    VIS_PRIVATE,
    CharacterRecord,
    CharacterStore,
    VersionRecord,
)

CARD_PREFIX = "characters"


class CharacterService:
    def __init__(self, ctx, store: CharacterStore, storage, chat_service) -> None:
        self.ctx = ctx
        self.store = store
        self.storage = storage
        self.chat = chat_service

    # ---------------- 导入 ----------------

    async def import_card(self, user_id: int, filename: str, data: bytes) -> CharacterRecord:
        settings = self.ctx.settings
        name = (filename or "card").lower()
        is_png = data.startswith(b"\x89PNG") or name.endswith(".png")
        if is_png:
            limit = settings.card_max_png_mb * 1024 * 1024
            if len(data) > limit:
                raise CardParseError(f"PNG 不能超过 {settings.card_max_png_mb} MB")
            card_json, _ = extract_card_json_from_png(data)
            card_png = data
            source = "png"
        else:
            limit = settings.card_max_json_mb * 1024 * 1024
            if len(data) > limit:
                raise CardParseError(f"JSON 不能超过 {settings.card_max_json_mb} MB")
            try:
                card_json = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CardParseError(f"JSON 解析失败：{exc}") from exc
            card_png = None
            source = "json"

        parsed = normalize_card(card_json, card_png=card_png)

        token = uuid.uuid4().hex
        card_key = f"{CARD_PREFIX}/{token}/card.png"
        card_url = ""
        if card_png:
            card_url = await self.storage.put(card_key, card_png, "image/png")

        cover_url = ""
        avatar_png = card_png or make_placeholder_png()
        avatar_webp = self._make_avatar(avatar_png)
        if avatar_webp:
            cover_url = await self.storage.put(
                f"{CARD_PREFIX}/{token}/avatar.webp", avatar_webp, "image/webp"
            )

        char = await self.store.create_character(
            creator_id=user_id,
            name=parsed.name,
            nickname=parsed.nickname,
            summary=parsed.summary,
            cover_url=cover_url,
            tags=parsed.tags,
            visibility=VIS_PRIVATE,
            status=STATUS_ONLINE,
            card_spec=parsed.spec,
            card_spec_version=parsed.spec_version,
            import_source=source,
        )
        version = await self.store.add_version(
            character_id=char.id,
            persona=parsed.persona,
            greeting=parsed.greeting,
            alternate_greetings=parsed.alternate_greetings,
            dialogue_samples=parsed.dialogue_samples,
            raw_card=parsed.raw,
            card_file_key=card_key if card_url else "",
            card_file_url=card_url,
            change_note=f"导入 {source.upper()} 卡",
            audit_status=2,
            created_by=user_id,
        )
        await self.store.set_current_version(char.id, version.id)
        if parsed.lorebook:
            await self.store.save_lorebook(char.id, parsed.lorebook)
        if cover_url:
            await self.store.add_media(char.id, 1, cover_url, asset_name="main", ext="webp")
        # 导入者自动成为创作者
        try:
            await self.ctx.repo.update_user(user_id, is_creator=True)
        except Exception:  # noqa: BLE001
            pass
        return await self.store.get(char.id) or char

    # ---------------- 查询 ----------------

    async def list_mine(self, user_id: int) -> list[CharacterRecord]:
        return await self.store.list_by_creator(user_id)

    async def require_owned(self, user_id: int, character_id: int) -> CharacterRecord:
        char = await self.store.get(character_id)
        if char is None:
            raise LookupError("角色不存在")
        if char.creator_id != user_id:
            raise PermissionError("只能操作自己导入的角色")
        return char

    async def detail(self, user_id: int, character_id: int) -> dict[str, Any]:
        char = await self.require_owned(user_id, character_id)
        version = await self.store.get_current_version(char.id)
        lorebook = await self.store.get_lorebook(char.id)
        media = await self.store.list_media(char.id)
        return {
            "character": char.public(),
            "version": _version_public(version),
            "lorebook": lorebook,
            "media": media,
        }

    # ---------------- 导出 ----------------

    async def export_json(self, user_id: int, character_id: int) -> dict[str, Any]:
        char = await self.require_owned(user_id, character_id)
        version = await self.store.get_current_version(char.id)
        if version is None:
            raise LookupError("角色没有可用版本")
        lorebook = await self.store.get_lorebook(char.id)
        return build_export_card(
            persona=version.persona,
            raw_card=version.raw_card,
            greeting=version.greeting,
            alternate_greetings=version.alternate_greetings,
            lorebook=lorebook,
            nickname=char.nickname,
        )

    async def export_png(self, user_id: int, character_id: int) -> tuple[bytes, str]:
        char = await self.require_owned(user_id, character_id)
        version = await self.store.get_current_version(char.id)
        if version is None:
            raise LookupError("角色没有可用版本")
        card = await self.export_json(user_id, character_id)
        base_png = None
        if version.card_file_key:
            try:
                base_png = await self.storage.get(version.card_file_key)
            except FileNotFoundError:
                base_png = None
        if not base_png:
            base_png = make_placeholder_png()
        png = embed_card_into_png(base_png, card)
        safe = "".join(c for c in char.name if c.isalnum() or c in "-_ ").strip() or f"character-{char.id}"
        return png, f"{safe}.png"

    # ---------------- 会话 ----------------

    async def create_session(self, user_id: int, character_id: int) -> dict[str, Any]:
        char = await self.store.get(character_id)
        if char is None or char.status != STATUS_ONLINE:
            raise LookupError("角色不存在或已下架")
        version = await self.store.get_current_version(char.id)
        if version is None:
            raise LookupError("角色没有可用版本")
        session = await self.chat.create_session(None, user_id)
        record = await self.ctx.repo.get_session(session.session_id)
        if record is None:
            raise LookupError("会话创建失败")
        record.persona = _persona_from_card(version, char)
        record.character_id = char.id
        record.character_version_id = version.id
        await self.ctx.repo.save_session(record)
        if version.greeting:
            await self.ctx.repo.append_message(
                session.session_id, ChatMessage(role="assistant", content=version.greeting)
            )
        try:
            await self.store.bump_chat(char.id, new_user=True)
        except Exception:  # noqa: BLE001
            pass
        return {
            "session_id": session.session_id,
            "character_id": char.id,
            "character_version_id": version.id,
            "name": char.name,
            "greeting": version.greeting,
        }

    async def sync_session(self, user_id: int, session_id: str) -> dict[str, Any]:
        record = await self.ctx.repo.get_session(session_id)
        if record is None or record.user_id != user_id:
            raise PermissionError("无权操作该会话")
        if not record.character_id:
            raise LookupError("该会话没有绑定角色")
        char = await self.store.get(record.character_id)
        version = await self.store.get_current_version(record.character_id) if char else None
        if char is None or version is None:
            raise LookupError("角色已不可用")
        record.persona = _persona_from_card(version, char)
        record.character_version_id = version.id
        await self.ctx.repo.save_session(record)
        return {
            "session_id": session_id,
            "character_id": char.id,
            "character_version_id": version.id,
            "name": char.name,
        }

    async def update_meta(self, user_id: int, character_id: int, fields: dict[str, Any]) -> CharacterRecord:
        await self.require_owned(user_id, character_id)
        allowed = {
            k: v
            for k, v in fields.items()
            if v is not None and k in ("name", "nickname", "summary", "tags", "visibility", "status")
        }
        if "name" in allowed and not str(allowed["name"]).strip():
            raise ValueError("角色名不能为空")
        updated = await self.store.update(character_id, **allowed)
        return updated or await self.require_owned(user_id, character_id)

    async def add_version(self, user_id: int, character_id: int, payload: dict[str, Any]) -> VersionRecord:
        char = await self.require_owned(user_id, character_id)
        current = await self.store.get_current_version(character_id)
        persona = dict(current.persona) if current else {"name": char.name}
        incoming = payload.get("persona") or {}
        allowed = {
            "name", "nickname", "description", "personality", "scenario",
            "system_prompt", "post_history_instructions", "creator_notes",
            "creator", "character_version", "tags", "mes_example",
            "group_only_greetings", "extensions",
        }
        for key in allowed:
            if key in incoming and incoming[key] is not None:
                persona[key] = incoming[key]
        name = str(persona.get("name") or char.name).strip()
        if not name:
            raise ValueError("角色名不能为空")
        persona["name"] = name

        greeting = payload.get("greeting")
        if greeting is None:
            greeting = current.greeting if current else ""
        alt = payload.get("alternate_greetings")
        if alt is None:
            alt = list(current.alternate_greetings) if current else []
        samples = payload.get("dialogue_samples")
        if samples is None:
            samples = list(current.dialogue_samples) if current else []
        raw = dict(current.raw_card) if current else {}

        description = str(persona.get("description") or "")
        version = await self.store.add_version(
            character_id=character_id,
            persona=persona,
            greeting=greeting or "",
            alternate_greetings=list(alt or []),
            dialogue_samples=list(samples or []),
            raw_card=raw,
            card_file_key=current.card_file_key if current else "",
            card_file_url=current.card_file_url if current else "",
            change_note=(payload.get("change_note") or "")[:255],
            audit_status=2,
            created_by=user_id,
        )
        if payload.get("publish", True):
            await self.store.set_current_version(character_id, version.id)
            await self.store.update(
                character_id,
                name=name[:64],
                nickname=str(persona.get("nickname") or "")[:64],
                summary=(" ".join(description.split())[:180]),
                tags=list(persona.get("tags") or []),
            )
        return version

    # ---------------- 内部 ----------------

    def _make_avatar(self, png: bytes) -> bytes | None:
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            return None
        try:
            image = Image.open(io.BytesIO(png))
            image.load()
        except Exception:  # noqa: BLE001
            return None
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        width, height = image.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        size = self.ctx.settings.character_avatar_size
        image = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(
            buf,
            format="WEBP",
            quality=self.ctx.settings.character_avatar_quality,
            method=6,
        )
        return buf.getvalue()


def _version_public(version: VersionRecord | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "version_id": version.id,
        "version_no": version.version_no,
        "persona": version.persona,
        "greeting": version.greeting,
        "alternate_greetings": version.alternate_greetings,
        "dialogue_samples": version.dialogue_samples,
        "has_card_png": bool(version.card_file_url),
        "card_file_url": version.card_file_url,
        "audit_status": version.audit_status,
    }


def _persona_from_card(version: VersionRecord, char: CharacterRecord) -> Persona:
    p = version.persona or {}
    return Persona(
        name=str(p.get("name") or char.name),
        avatar=char.cover_url or None,
        personality=str(p.get("personality") or ""),
        speaking_style=str(p.get("speaking_style") or p.get("personality") or "")[:400],
        background=str(p.get("scenario") or p.get("description") or "")[:600],
        likes=list(p.get("tags") or [])[:8],
        dislikes=[],
        boundaries=[],
    )


__all__ = ["CharacterService", "CardParseError"]
