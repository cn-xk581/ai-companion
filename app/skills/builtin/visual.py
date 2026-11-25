"""Skill: image.reply / meme.reply —— 让她能发图、发表情（业务包装）。

边界：画什么、能不能画、配什么话，是业务；真正生成/检索是 MCP。
两张能力都做了内容兜底：露骨/违法提示词在调用外部服务前就被拒。
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.core.errors import BusinessRuleViolation
from app.domain.companion import MOOD_LABELS, STAGE_INTIMACY_LEVEL, RelationshipStage
from app.skills.base import Skill, SkillContext

_UNSAFE_PROMPT = re.compile(r"(裸|色情|porn|nude|naked|性|血腥|暴力|枪支|drugs?)", re.I)

_MOOD_TAGS: dict[str, list[str]] = {
    "sad": ["comfort", "cheer"],
    "tired": ["comfort", "goodnight"],
    "angry": ["comfort", "angry"],
    "shy": ["shy", "crush"],
    "happy": ["cheer"],
    "excited": ["cheer"],
    "neutral": ["care"],
}
_STAGE_LIMITED_TAGS = {"love", "couple", "crush"}


class ImageReplyInput(BaseModel):
    prompt: str = Field(min_length=2, max_length=300)
    style: str | None = Field(default="温柔插画", max_length=40)
    stage: str = "stranger"


class ImageReplyOutput(BaseModel):
    url: str
    caption: str
    prompt: str


class ImageReplySkill(Skill):
    name = "image.reply"
    version = "1.1.0"
    description = "按提示词生成一张图，并配一句符合关系阶段的话"
    owner = "companion-tools"
    tags = ["business", "tool"]
    Input = ImageReplyInput
    Output = ImageReplyOutput
    mcp_tools = ["image.generate"]
    timeout_sec = 30.0
    idempotent = False

    async def run(self, payload: ImageReplyInput, sctx: SkillContext) -> ImageReplyOutput:
        if _UNSAFE_PROMPT.search(payload.prompt):
            raise BusinessRuleViolation("这个画面我画不了", user_hint="换个画面吧")
        result = await sctx.call_mcp(
            "image.generate",
            {"prompt": payload.prompt, "style": payload.style or "温柔插画"},
            purpose="生成图片",
            declared=self.mcp_tools,
        )
        stage = _stage(payload.stage)
        caption = {
            RelationshipStage.STRANGER: "画好了，你看看像不像。",
            RelationshipStage.ACQUAINTANCE: "画完啦，别嫌我手笨。",
            RelationshipStage.CRUSH: "画了好久…才敢给你看。",
            RelationshipStage.DATING: "给你的，只此一张。",
            RelationshipStage.COUPLE: "我们的样子，我记下来了。",
        }[stage]
        await sctx.tracer.decision("image.generated", prompt=payload.prompt[:60])
        return ImageReplyOutput(url=result.data["url"], caption=caption, prompt=payload.prompt)


class MemeReplyInput(BaseModel):
    mood: str = "neutral"
    stage: str = "stranger"
    offset: int = Field(default=0, ge=0, le=50)


class MemeReplyOutput(BaseModel):
    emoji: str
    caption: str
    tags: list[str] = Field(default_factory=list)


class MemeReplySkill(Skill):
    name = "meme.reply"
    version = "1.1.0"
    description = "按情绪与关系阶段挑一条表情（文本+emoji）"
    owner = "companion-tools"
    tags = ["business", "tool"]
    Input = MemeReplyInput
    Output = MemeReplyOutput
    mcp_tools = ["meme.pick"]
    timeout_sec = 4.0

    async def run(self, payload: MemeReplyInput, sctx: SkillContext) -> MemeReplyOutput:
        stage = _stage(payload.stage)
        level = STAGE_INTIMACY_LEVEL[stage]
        tags = list(_MOOD_TAGS.get(payload.mood, ["care"]))
        if level < 2:
            tags = [t for t in tags if t not in _STAGE_LIMITED_TAGS]

        result = await sctx.call_mcp(
            "meme.pick",
            {"tags": tags, "limit": 3, "offset": payload.offset},
            purpose="挑表情",
            declared=self.mcp_tools,
        )
        items = result.data.get("items") or []
        if not items:
            raise BusinessRuleViolation("没有合适的表情", user_hint="这次就不发表情了")
        picked = items[0]
        await sctx.tracer.decision(
            "meme.picked", mood=payload.mood, tags=tags, picked=picked.get("id")
        )
        return MemeReplyOutput(
            emoji=picked.get("emoji", "🙂"),
            caption=picked.get("caption", ""),
            tags=list(picked.get("tags") or []),
        )


def _stage(value: str) -> RelationshipStage:
    try:
        return RelationshipStage(value)
    except ValueError:
        return RelationshipStage.STRANGER


__all__ = ["ImageReplySkill", "MemeReplySkill", "MOOD_LABELS"]
