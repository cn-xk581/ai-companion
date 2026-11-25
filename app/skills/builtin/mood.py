"""Skill: mood.update —— 情绪状态机（确定性规则，不交给模型「感觉」）。

情绪是持久状态：用户哄她会变好、吵架会变差、被夸会害羞；强度有衰减，
避免一次「我错了」就让她开心一整周。
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.domain.companion import MOOD_LABELS, MoodState
from app.skills.base import Skill, SkillContext

_POSITIVE = re.compile(r"(想你|爱你|喜欢|乖|真棒|好看|漂亮|可爱|抱抱|哄|夸|宝贝|亲爱的|老婆|老公|晚安|早安|谢谢)")
_NEGATIVE = re.compile(r"(滚|讨厌|烦|分手|绝交|拉黑|冷漠|敷衍|不爱|嫌弃|别烦|闭嘴|吵|失望|拜拜)")
_SOFTEN = re.compile(r"(对不起|抱歉|我错了|别生气|原谅|哄你|认错)")

#: 正向刺激时，从当前情绪迁到哪个情绪
_SHIFT_UP = {
    "happy": "happy",
    "excited": "excited",
    "shy": "shy",
    "neutral": "happy",
    "sad": "happy",
    "angry": "neutral",
    "tired": "happy",
}


class MoodInput(BaseModel):
    text: str = Field(max_length=4000)
    mood: MoodState = Field(default_factory=MoodState)


class MoodOutput(BaseModel):
    mood: MoodState
    label: str
    delta: float
    reason: str = ""


class MoodUpdateSkill(Skill):
    name = "mood.update"
    version = "1.1.0"
    description = "按本轮输入微调她的情绪与强度（正向/负向/哄，带衰减）"
    owner = "companion-core"
    tags = ["business", "state"]
    Input = MoodInput
    Output = MoodOutput
    mcp_tools = []
    timeout_sec = 2.0

    async def run(self, payload: MoodInput, sctx: SkillContext) -> MoodOutput:
        text = payload.text or ""
        delta = 0.0
        reason = ""

        if _SOFTEN.search(text):
            delta += 0.25
            reason = "用户示好"
        if _POSITIVE.search(text):
            delta += 0.15
            reason = reason or "正向表达"
        if _NEGATIVE.search(text):
            delta -= 0.3
            reason = "负面表达"

        # 无信号时缓慢回落（强度衰减，不改变情绪类型）
        if delta == 0.0:
            decayed = max(0.2, payload.mood.mood_intensity - 0.05)
            mood = MoodState(current_mood=payload.mood.current_mood, mood_intensity=decayed)
            return MoodOutput(mood=mood, label=MOOD_LABELS.get(mood.current_mood, mood.current_mood), delta=0.0)

        intensity = max(0.0, min(1.0, payload.mood.mood_intensity + delta))
        current = payload.mood.current_mood
        if delta >= 0.15:
            current = _SHIFT_UP.get(current, "happy")
        elif delta <= -0.25:
            current = "angry" if intensity >= 0.5 else "sad"

        mood = MoodState(current_mood=current, mood_intensity=round(intensity, 2))
        await sctx.tracer.decision(
            "mood.updated", mood=current, intensity=round(intensity, 2), delta=round(delta, 2), reason=reason
        )
        return MoodOutput(
            mood=mood,
            label=MOOD_LABELS.get(current, current),
            delta=round(delta, 2),
            reason=reason,
        )
