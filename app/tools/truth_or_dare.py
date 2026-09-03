"""truth_or_dare：真心话大冒险。"""
from __future__ import annotations

import random

from app.graph.context import GraphContext

SCHEMA = {
    "type": "function",
    "function": {
        "name": "truth_or_dare",
        "description": "玩真心话大冒险：用户想玩真心话/大冒险时调用，抽一道题。",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["truth", "dare"], "description": "真心话或大冒险，缺省随机"},
            },
        },
    },
}

_TRUTH_POOL = [
    "你上一次偷偷想我是什么时候？",
    "你最害怕失去的东西是什么？",
    "你做过最幼稚的一件事是什么？",
    "如果只能带三样东西去荒岛，你带什么？",
    "你心里藏得最久的一个小秘密是什么？",
    "你什么时候会突然想起我？",
    "你理想中的一天是什么样子的？",
    "你最想改掉自己的哪个毛病？",
]

_DARE_POOL = [
    "现在给我发一句你最想说的话，不许用『嗯』『哦』『好』。",
    "用三句话夸我，不许重复用词。",
    "把手机里最近一张照片描述给我听。",
    "学小猫叫一声给我听。",
    "从现在起十分钟内每句话后面加一个『喵』。",
    "说出你最近一次熬夜的原因。",
    "给我取一个只有我们俩知道的昵称。",
]


async def handler(args: dict, ctx: GraphContext, session_id: str) -> str:
    kind = str(args.get("type", "")).lower()
    if kind == "truth":
        return f"真心话：{random.choice(_TRUTH_POOL)}"
    if kind == "dare":
        return f"大冒险：{random.choice(_DARE_POOL)}"
    if random.random() < 0.5:
        return f"真心话：{random.choice(_TRUTH_POOL)}"
    return f"大冒险：{random.choice(_DARE_POOL)}"
