"""send_meme：发送表情包（演示：本地素材池 / 对象存储占位）。"""
from __future__ import annotations

import random

from app.graph.context import GraphContext

SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_meme",
        "description": "发送一张表情包。用户要表情包/斗图/发图时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "表情包主题关键词，如：开心、委屈、生气、晚安"},
            },
        },
    },
}

_MEME_POOL: list[dict] = [
    {"name": "猫猫撒娇", "url": "https://example.invalid/meme/cat-cute.png", "tags": ["开心", "撒娇", "想你"]},
    {"name": "委屈巴巴", "url": "https://example.invalid/meme/pout.png", "tags": ["委屈", "生气", "难过"]},
    {"name": "晚安月亮", "url": "https://example.invalid/meme/goodnight.png", "tags": ["晚安", "睡觉"]},
    {"name": "傲娇扭头", "url": "https://example.invalid/meme/tsundere.png", "tags": ["傲娇", "生气", "哼"]},
    {"name": "比心", "url": "https://example.invalid/meme/heart.png", "tags": ["爱你", "开心", "想你"]},
]


async def handler(args: dict, ctx: GraphContext, session_id: str) -> str:
    keyword = str(args.get("keyword", "")).strip()
    if keyword:
        matched = [m for m in _MEME_POOL if any(t in keyword for t in m["tags"]) or keyword in m["name"]]
        if matched:
            meme = random.choice(matched)
            return f"给你一个「{meme['name']}」：{meme['url']}"
    meme = random.choice(_MEME_POOL)
    return f"给你一个「{meme['name']}」：{meme['url']}"
