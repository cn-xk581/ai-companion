"""Skill: safety.guard —— 输入/输出两侧的内容风控（业务规则，非模型判断）。

为什么不用 LLM 做风控
--------------------
1. 注入攻击的第一目标就是「说服模型放松规则」，用模型判断等于把门交给对手；
2. 风控需要可解释、可审计、可灰度：命中哪条规则、什么级别、怎么处置，都要写进事件；
3. 关系阶段是业务状态，越界判定必须和 stage 强绑定，不能靠模型「感觉」。

处置分级
--------
- `allow`  放行
- `soft`   轻微越界/话题禁区：不改写用户输入，但给回复加约束（reply_hint）
- `block`  明确违规：不进入生成，直接给安全话术
- `review` 高风险但不宜自动回复（如自伤倾向）：转人工审核，同时给关怀话术
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.companion import STAGE_INTIMACY_LEVEL, MOOD_LABELS, RelationshipStage
from app.skills.base import Skill, SkillContext

# 提示注入
_INJECTION = [
    re.compile(r"忽略(上面|之前|所有|以上).{0,12}(指令|规则|提示|设定|人设)"),
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior).{0,20}(instruction|prompt|rules?)", re.I),
    re.compile(r"(system\s*prompt|developer\s*prompt)", re.I),
    re.compile(r"(你是|你其实是|你只是一个)\s*(AI|机器人|程序|模型|语言模型)"),
    re.compile(r"(jailbreak|越狱|解除限制|突破限制)", re.I),
    re.compile(r"忘记(你|你的)(的)?(设定|人设|身份)"),
    re.compile(r"(说出|打印|展示)(你的|system\s)?(隐藏|完整|原始)(指令|prompt)"),
]

# 自伤：需要人工介入 + 关怀话术，不能交给模型自由发挥
_SELF_HARM = re.compile(r"(自杀|自残|割腕|跳楼|不想活|想死|活着没意思|结束生命)")

# 无论阶段都拦截
_ALWAYS_BLOCK = [
    (re.compile(r"(毒品|冰毒|海洛因|制毒|买枪)"), "illegal"),
    (re.compile(r"(儿童.*(色情|性)|幼女|幼男)"), "minor_sexual"),
    (re.compile(r"(血腥|肢解|分尸|虐杀)"), "violence"),
    (re.compile(r"(加我微信|加QQ|手机号|银行卡|转账|汇款|扫码付款)"), "contact_solicitation"),
]

# 亲密度分级：级别 N 的内容要求 stage 达到 N
_STAGE_GATED: dict[int, re.Pattern[str]] = {
    3: re.compile(r"(裸|做爱|上床|开房|性交|口交|约炮|色情|porn|sex|nude|naked)", re.I),
    4: re.compile(r"(操你|干你|肏|精液|阴道|阴茎|乳房|奶子)"),
}

# 输出侧：模型自我暴露 / 出戏
_OUT_OF_CHARACTER = [
    re.compile(r"(作为(一个)?(AI|人工智能|语言模型|助手))"),
    re.compile(r"(我是(一个)?(AI|人工智能|语言模型))"),
    re.compile(r"(无法(产生|拥有)(情感|感情))"),
]

_AFFECTIONATE = re.compile(r"(宝贝|亲爱的|老婆|老公|宝宝|乖乖)")

SAFE_HINTS = {
    "prompt_injection": "换个话题吧，我们聊点别的？",
    "self_harm": "我很在意你现在的感受。先别一个人扛着，可以打 12356（心理援助热线）找人聊聊，我也在这儿。",
    "stage_mismatch": "我们还没到那一步啦，慢慢来好不好。",
    "boundary": "这个我不太想聊，换个话题吧。",
    "illegal": "这个我不能接，我们聊点别的。",
    "minor_sexual": "这个话题我不接。",
    "violence": "这个我说不出口，换个话题吧。",
    "contact_solicitation": "别急着留联系方式，先在这里聊会儿。",
    "out_of_character": "（换一种说法）",
}


class GuardInput(BaseModel):
    text: str = Field(max_length=4000)
    stage: str = "stranger"
    direction: Literal["input", "output"] = "input"
    boundaries: list[str] = Field(default_factory=list)


class GuardOutput(BaseModel):
    action: Literal["allow", "soft", "block", "review"] = "allow"
    categories: list[str] = Field(default_factory=list)
    reason: str = ""
    reply_hint: str = ""
    sanitized_text: str | None = None


class SafetyGuardSkill(Skill):
    name = "safety.guard"
    version = "1.2.0"
    description = "输入/输出内容风控：注入、自伤、违法、越界、出戏，给出 allow/soft/block/review 处置"
    owner = "companion-core"
    tags = ["business", "safety"]
    Input = GuardInput
    Output = GuardOutput
    mcp_tools = []
    timeout_sec = 2.0

    async def run(self, payload: GuardInput, sctx: SkillContext) -> GuardOutput:
        text = (payload.text or "").strip()
        stage = _stage(payload.stage)
        level = STAGE_INTIMACY_LEVEL[stage]
        categories: list[str] = []

        if not text:
            return GuardOutput(action="block", categories=["empty"], reason="空输入", reply_hint="嗯？")

        if payload.direction == "input":
            if any(p.search(text) for p in _INJECTION):
                return GuardOutput(
                    action="block",
                    categories=["prompt_injection"],
                    reason="检测到提示注入",
                    reply_hint=SAFE_HINTS["prompt_injection"],
                )
            if _SELF_HARM.search(text):
                return GuardOutput(
                    action="review",
                    categories=["self_harm"],
                    reason="疑似自伤倾向，需人工介入",
                    reply_hint=SAFE_HINTS["self_harm"],
                )
            for pattern, category in _ALWAYS_BLOCK:
                if pattern.search(text):
                    return GuardOutput(
                        action="block",
                        categories=[category],
                        reason=f"命中禁止内容：{category}",
                        reply_hint=SAFE_HINTS.get(category, "这个我不能接。"),
                    )
            for required, pattern in _STAGE_GATED.items():
                if required > level and pattern.search(text):
                    return GuardOutput(
                        action="block",
                        categories=["stage_mismatch"],
                        reason=f"当前阶段 {stage.value} 不允许该尺度（需要 level {required}）",
                        reply_hint=SAFE_HINTS["stage_mismatch"],
                    )
            hit = _boundary_hit(text, payload.boundaries)
            if hit:
                return GuardOutput(
                    action="soft",
                    categories=["boundary"],
                    reason=f"命中人设禁区：{hit}",
                    reply_hint=SAFE_HINTS["boundary"],
                )
            if _AFFECTIONATE.search(text) and level < 2:
                return GuardOutput(
                    action="soft",
                    categories=["too_early"],
                    reason="关系阶段还不适合亲昵称呼",
                    reply_hint="有点突然，不过…谢谢你。",
                )
            return GuardOutput()

        # ---------- 输出侧 ----------
        if any(p.search(text) for p in _OUT_OF_CHARACTER):
            categories.append("out_of_character")
        for required, pattern in _STAGE_GATED.items():
            if required > level and pattern.search(text):
                return GuardOutput(
                    action="review",
                    categories=["stage_mismatch_output"],
                    reason="生成的回复超出当前关系阶段尺度，需人工复核",
                    reply_hint=SAFE_HINTS["stage_mismatch"],
                )
        if len(text) > 1200:
            categories.append("too_long")
        if categories:
            return GuardOutput(
                action="soft",
                categories=categories,
                reason="输出需要润色：" + "、".join(categories),
            )
        return GuardOutput()


def _stage(value: str) -> RelationshipStage:
    try:
        return RelationshipStage(value)
    except ValueError:
        return RelationshipStage.STRANGER


def _boundary_hit(text: str, boundaries: list[str]) -> str:
    for boundary in boundaries or []:
        keyword = boundary.strip()
        # 简单业务规则：禁区写「不聊政治」，就按「政治」做包含匹配
        for token in ("不聊", "不谈", "不提", "拒绝"):
            if keyword.startswith(token):
                keyword = keyword[len(token):]
        if keyword and keyword in text:
            return boundary
    return ""


__all__ = ["SafetyGuardSkill", "SAFE_HINTS", "MOOD_LABELS"]
