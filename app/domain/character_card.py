"""角色卡解析/生成：兼容 SillyTavern / Character Card V1 / V2 / V3 与 PNG 内嵌 tEXt。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CARD_CHUNK_KEYS = ("ccv3", "chara")


class CardParseError(ValueError):
    """卡格式不合法。"""


@dataclass
class ParsedCard:
    spec: str = "chara_card_v2"
    spec_version: str = "2.0"
    raw: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    nickname: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    persona: dict[str, Any] = field(default_factory=dict)
    greeting: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    group_only_greetings: list[str] = field(default_factory=list)
    dialogue_samples: list[dict[str, str]] = field(default_factory=list)
    lorebook: dict[str, Any] | None = None
    card_png: bytes | None = None
    avatar_png: bytes | None = None


# ---------------- PNG ----------------


def _iter_png_chunks(data: bytes):
    if not data.startswith(_PNG_SIGNATURE):
        raise CardParseError("不是有效的 PNG 文件")
    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(data):
            break
        yield tag, data[start:end]
        offset = end + 4
        if tag == b"IEND":
            break


def extract_card_json_from_png(data: bytes) -> tuple[dict[str, Any], str]:
    """返回 (card_json, 来源 chunk)。优先 ccv3，其次 chara。"""
    found: dict[str, dict[str, Any]] = {}
    for tag, payload in _iter_png_chunks(data):
        if tag != b"tEXt" or b"\x00" not in payload:
            continue
        keyword, _, text = payload.partition(b"\x00")
        key = keyword.decode("latin-1", "ignore")
        if key not in _CARD_CHUNK_KEYS or key in found:
            continue
        try:
            raw = base64.b64decode(text, validate=False)
            found[key] = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CardParseError(f"PNG 里的角色卡 JSON 解析失败（{key}）：{exc}") from exc
    if "ccv3" in found:
        return found["ccv3"], "ccv3"
    if "chara" in found:
        return found["chara"], "chara"
    raise CardParseError("PNG 里没有找到角色卡数据（tEXt: ccv3/chara）")


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)


def embed_card_into_png(base_png: bytes, card: dict[str, Any]) -> bytes:
    """把卡 JSON 写进 PNG 的 ccv3 与 chara 两个 tEXt chunk（兼容 V2/V3 客户端）。"""
    if not base_png.startswith(_PNG_SIGNATURE):
        raise CardParseError("底图不是 PNG")
    payload = base64.b64encode(
        json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    chunks = [_png_chunk(b"tEXt", b"ccv3\x00" + payload), _png_chunk(b"tEXt", b"chara\x00" + payload)]
    out = bytearray(_PNG_SIGNATURE)
    inserted = False
    for tag, body in _iter_png_chunks(base_png):
        if tag == b"IEND" and not inserted:
            out.extend(b"".join(chunks))
            inserted = True
        out.extend(_png_chunk(tag, body))
    if not inserted:
        raise CardParseError("PNG 缺少 IEND")
    return bytes(out)


def make_placeholder_png(color: tuple[int, int, int] = (30, 40, 36), size: int = 512) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise CardParseError("缺少 Pillow，无法生成占位图") from exc
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------- JSON / 归一化 ----------------


def _as_str(value: Any, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").strip()
    return text[:limit] if limit else text


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _summary_of(description: str) -> str:
    flat = " ".join(description.split())
    return flat[:180]


def _parse_samples(mes_example: str) -> list[dict[str, str]]:
    """把 V2/V3 的 mes_example（<START> 分隔）解析成 [{user, assistant}]。"""
    samples: list[dict[str, str]] = []
    for block in mes_example.split("<START>"):
        user_line, char_line = "", ""
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("{{user}}:"):
                user_line = line[len("{{user}}:") :].strip()
            elif line.startswith("{{char}}:"):
                char_line = line[len("{{char}}:") :].strip()
        if user_line or char_line:
            samples.append({"user": user_line, "assistant": char_line})
    return samples


def _normalize_lorebook(book: Any) -> dict[str, Any] | None:
    if not isinstance(book, dict):
        return None
    entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(_as_list(book.get("entries"))):
        if not isinstance(entry, dict):
            continue
        content = _as_str(entry.get("content"))
        if not content:
            continue
        entries.append(
            {
                "uid": _as_str(entry.get("id", idx), 64) or str(idx),
                "name": _as_str(entry.get("name") or entry.get("comment"), 128),
                "keys": [str(k) for k in _as_list(entry.get("keys")) if str(k)],
                "secondary_keys": [str(k) for k in _as_list(entry.get("secondary_keys")) if str(k)],
                "content": content,
                "enabled": bool(entry.get("enabled", True)),
                "constant": bool(entry.get("constant", False)),
                "selective": bool(entry.get("selective", False)),
                "insertion_order": int(entry.get("insertion_order", 100) or 100),
                "position": _as_str(entry.get("position") or "after_char", 24),
                "case_sensitive": entry.get("case_sensitive"),
                "use_regex": bool(entry.get("use_regex", False)),
                "priority": entry.get("priority"),
                "comment": _as_str(entry.get("comment"), 255),
                "extensions": entry.get("extensions") if isinstance(entry.get("extensions"), dict) else {},
            }
        )
    if not entries:
        return None
    return {
        "name": _as_str(book.get("name"), 128),
        "description": _as_str(book.get("description")),
        "scan_depth": book.get("scan_depth") if isinstance(book.get("scan_depth"), int) else None,
        "token_budget": book.get("token_budget") if isinstance(book.get("token_budget"), int) else None,
        "recursive_scanning": book.get("recursive_scanning")
        if isinstance(book.get("recursive_scanning"), bool)
        else None,
        "extensions": book.get("extensions") if isinstance(book.get("extensions"), dict) else {},
        "entries": entries,
    }


def normalize_card(card: dict[str, Any], card_png: bytes | None = None) -> ParsedCard:
    """V1（扁平）/ V2（chara_card_v2）/ V3（chara_card_v3）→ ParsedCard。"""
    if not isinstance(card, dict):
        raise CardParseError("角色卡必须是 JSON 对象")
    spec = _as_str(card.get("spec"), 32)
    spec_version = _as_str(card.get("spec_version"), 16)
    if isinstance(card.get("data"), dict):
        data = card["data"]
    else:
        data = card  # V1 扁平结构
        spec, spec_version = spec or "chara_card_v1", spec_version or "1.0"
    name = _as_str(data.get("name"), 64)
    if not name:
        raise CardParseError("角色卡缺少 name")

    description = _as_str(data.get("description"))
    persona = {
        "name": name,
        "nickname": _as_str(data.get("nickname"), 64),
        "description": description,
        "personality": _as_str(data.get("personality")),
        "scenario": _as_str(data.get("scenario")),
        "first_mes": _as_str(data.get("first_mes")),
        "mes_example": _as_str(data.get("mes_example")),
        "system_prompt": _as_str(data.get("system_prompt")),
        "post_history_instructions": _as_str(data.get("post_history_instructions")),
        "creator_notes": _as_str(data.get("creator_notes")),
        "creator": _as_str(data.get("creator"), 128),
        "character_version": _as_str(data.get("character_version"), 32),
        "tags": [str(t) for t in _as_list(data.get("tags")) if str(t)],
        "alternate_greetings": [_as_str(g) for g in _as_list(data.get("alternate_greetings")) if _as_str(g)],
        "group_only_greetings": [
            _as_str(g) for g in _as_list(data.get("group_only_greetings")) if _as_str(g)
        ],
        "extensions": data.get("extensions") if isinstance(data.get("extensions"), dict) else {},
    }
    if data.get("assets"):
        persona["assets"] = data["assets"]
    if data.get("source"):
        persona["source"] = data["source"]

    return ParsedCard(
        spec=spec or "chara_card_v2",
        spec_version=spec_version or "2.0",
        raw=data,
        name=name,
        nickname=persona["nickname"],
        summary=_summary_of(description),
        tags=persona["tags"],
        persona=persona,
        greeting=persona["first_mes"],
        alternate_greetings=persona["alternate_greetings"],
        group_only_greetings=persona["group_only_greetings"],
        dialogue_samples=_parse_samples(persona["mes_example"]),
        lorebook=_normalize_lorebook(data.get("character_book")),
        card_png=card_png,
    )


def build_export_card(
    *,
    persona: dict[str, Any],
    raw_card: dict[str, Any],
    greeting: str,
    alternate_greetings: list[str],
    lorebook: dict[str, Any] | None,
    nickname: str = "",
) -> dict[str, Any]:
    """把库里的数据拼回 V3 卡；以 raw_card 为底，保证未知字段不丢。"""
    data: dict[str, Any] = dict(raw_card or {})
    data.update(
        {
            "name": persona.get("name") or data.get("name", ""),
            "nickname": nickname or persona.get("nickname", ""),
            "description": persona.get("description", ""),
            "personality": persona.get("personality", ""),
            "scenario": persona.get("scenario", ""),
            "first_mes": greeting or persona.get("first_mes", ""),
            "mes_example": persona.get("mes_example", ""),
            "system_prompt": persona.get("system_prompt", ""),
            "post_history_instructions": persona.get("post_history_instructions", ""),
            "creator_notes": persona.get("creator_notes", ""),
            "creator": persona.get("creator", ""),
            "character_version": persona.get("character_version", ""),
            "tags": list(persona.get("tags") or []),
            "alternate_greetings": list(alternate_greetings or []),
            "group_only_greetings": list(persona.get("group_only_greetings") or []),
            "extensions": persona.get("extensions") or {},
        }
    )
    if lorebook:
        data["character_book"] = {
            "name": lorebook.get("name") or "",
            "entries": [
                {
                    "id": e.get("uid"),
                    "name": e.get("name"),
                    "keys": e.get("keys") or [],
                    "secondary_keys": e.get("secondary_keys") or [],
                    "content": e.get("content") or "",
                    "enabled": bool(e.get("enabled", True)),
                    "constant": bool(e.get("constant", False)),
                    "selective": bool(e.get("selective", False)),
                    "insertion_order": int(e.get("insertion_order") or 100),
                    "position": e.get("position") or "after_char",
                    "case_sensitive": e.get("case_sensitive"),
                    "use_regex": bool(e.get("use_regex", False)),
                    "comment": e.get("comment") or "",
                    "extensions": e.get("extensions") or {},
                }
                for e in lorebook.get("entries") or []
            ],
            "extensions": lorebook.get("extensions") or {},
        }
        if lorebook.get("description"):
            data["character_book"]["description"] = lorebook["description"]
        if lorebook.get("scan_depth") is not None:
            data["character_book"]["scan_depth"] = lorebook["scan_depth"]
        if lorebook.get("token_budget") is not None:
            data["character_book"]["token_budget"] = lorebook["token_budget"]
        if lorebook.get("recursive_scanning") is not None:
            data["character_book"]["recursive_scanning"] = lorebook["recursive_scanning"]
    else:
        data.pop("character_book", None)

    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}


def card_fingerprint(card: dict[str, Any]) -> str:
    blob = json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
