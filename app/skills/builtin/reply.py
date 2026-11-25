"""Skill: reply.polish —— 回复的业务后处理（长度/出戏/尺度/称呼）。

LLM 负责「说什么」，这里负责「能不能这么说」：
- 去掉出戏句（"作为 AI…"）、markdown 残留；
- 关系阶段不够时收回亲昵称呼；
- 超长按句子截断（不是硬切字符）；
- 空回复兜底，保证前端永远有话说。
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.domain.companion import STAGE_INTIMACY_LEVEL, RelationshipStage
from app.skills.base import Skill, SkillContext

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…~])")
_OUT_OF_CHARACTER = re.compile(
    r"(作为(一个)?(AI|人工智能|语言模型|助手)[^。！？!?]*[。！？!?]?)|"
    r"(我是(一个)?(AI|人工智能|语言模型)[^。！？!?]*[。！？!?]?)|"
    r"(无法(产生|拥有)(情感|感情)[^。！？!?]*[。！？!?]?)"
)
_AFFECTIONATE = re.compile(r"(宝贝|亲爱的|老婆|老公|宝宝|乖乖)")
_MARKDOWN = re.compile(r"^\s*([-*+]|\d+[.)])\s+", re.M)
_FALLBACK = "嗯，我在。"


class PolishInput(BaseModel):
    draft: str = Field(default="", max_length=4000)
    stage: str = "stranger"
    persona_name: str = "小柔"
    max_chars: int = Field(default=220, ge=40, le=1200)


class PolishOutput(BaseModel):
    text: str
    changes: list[str] = Field(default_factory=list)
    truncated: bool = False


def _stage(value: str) -> RelationshipStage:
    try:
        return RelationshipStage(value)
    except ValueError:
        return RelationshipStage.STRANGER


class ReplyPolishSkill(Skill):
    name = "reply.polish"
    version = "1.1.0"
    description = "回复后处理：去出戏/markdown、按阶段收称呼、按句截断、空回复兜底"
    owner = "companion-core"
    tags = ["business", "reply"]
    Input = PolishInput
    Output = PolishOutput
    mcp_tools = []
    timeout_sec = 2.0

    async def run(self, payload: PolishInput, sctx: SkillContext) -> PolishOutput:
        text = (payload.draft or "").strip()
        changes: list[str] = []
        stage = _stage(payload.stage)
        level = STAGE_INTIMACY_LEVEL[stage]

        if not text:
            return PolishOutput(text=_FALLBACK, changes=["空回复兜底"])

        cleaned = _OUT_OF_CHARACTER.sub("", text).strip()
        if cleaned != text:
            changes.append("移除出戏表述")
            text = cleaned

        if _MARKDOWN.search(text):
            text = _MARKDOWN.sub("", text)
            changes.append("移除 markdown 列表标记")

        if level < 2 and _AFFECTIONATE.search(text):
            text = _AFFECTIONATE.sub("", text)
            text = re.sub(r"[，,]\s*[，,]", "，", text)
            changes.append("关系阶段不够，收回亲昵称呼")

        # 压缩多余空行与空格
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()

        truncated = False
        if len(text) > payload.max_chars:
            head = text[: payload.max_chars]
            sentences = [s for s in _SENTENCE_SPLIT.split(head) if s]
            if len(sentences) > 1:
                head = "".join(sentences[:-1])
            text = head.rstrip("，,、；;") + "…"
            truncated = True
            changes.append(f"超过 {payload.max_chars} 字，已按句截断")

        if not text.strip():
            text = _FALLBACK
            changes.append("清洗后为空，回退兜底话术")

        await sctx.tracer.decision("reply.polished", changes=changes, truncated=truncated, length=len(text))
        return PolishOutput(text=text, changes=changes, truncated=truncated)
