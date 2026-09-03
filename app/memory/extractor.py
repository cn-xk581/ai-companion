"""记忆抽取：LLM 从本轮对话提取值得记住的事实，避免记忆库垃圾膨胀。"""
from __future__ import annotations

import json
import logging

from app.graph.state import MemoryItem
from app.llm.base import LLMClient
from app.llm.prompts import build_extract_messages

logger = logging.getLogger(__name__)

MIN_IMPORTANCE = 0.1  # 低于该权重直接丢弃


async def extract_memories(llm: LLMClient, user_input: str, reply: str) -> list[MemoryItem]:
    """从本轮 user_input + reply 中抽取长期记忆。解析失败时返回空列表（宁可漏记，不写垃圾）。"""
    try:
        result = await llm.complete(
            build_extract_messages(user_input, reply),
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory extract failed: %s", exc)
        return []

    data = _parse_json(result.content)
    raw_memories = (data or {}).get("memories", [])
    if not isinstance(raw_memories, list):
        return []

    memories: list[MemoryItem] = []
    for item in raw_memories:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        if importance < MIN_IMPORTANCE:
            continue
        category = str(item.get("category", "fact"))
        tags = [str(t) for t in (item.get("tags") or [])][:8]
        memories.append(
            MemoryItem(content=content, importance=round(min(1.0, max(0.0, importance)), 3), category=category, tags=tags)
        )
    return memories


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 容忍模型在 JSON 前后加 ```json ... ``` 或说明文字
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
