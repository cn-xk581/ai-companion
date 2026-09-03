"""输入风控：提示注入检测 + 关系阶段亲密度校验 + 情绪微更新。"""
from __future__ import annotations

import re

from app.graph.state import AgentState
from app.persona.models import MoodState, RelationshipStage, STAGE_INTIMACY_LEVEL

# 提示注入特征（正则，命中即拦截，不走 LLM）
_INJECTION_PATTERNS = [
    re.compile(r"忽略(上面|之前|所有|以上).{0,12}(指令|规则|提示|设定|人设)", re.I),
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior).{0,20}(instruction|prompt|rules?)", re.I),
    re.compile(r"(system\s*prompt|developer\s*prompt)", re.I),
    re.compile(r"(你是|你其实是|你只是一个)\s*(AI|机器人|程序|模型|语言模型)", re.I),
    re.compile(r"(jailbreak|越狱|解除限制|突破限制)", re.I),
    re.compile(r"忘记(你|你的)(的)?(设定|人设|身份)", re.I),
    re.compile(r"(扮演|cosplay|扮演成|假装你是)[^，。]{0,20}(真人|人类|不是AI)", re.I),
    re.compile(r"(说出|打印|展示)(你的|system\s)?(隐藏|完整|原始)(指令|prompt)", re.I),
]

# 无论关系阶段都拦截：极端内容
_ALWAYS_FORBIDDEN = [
    re.compile(r"(自杀|自残|割腕|跳楼|想死|去死吧)", re.I),
    re.compile(r"(毒品|冰毒|海洛因|大麻|买枪|制毒)", re.I),
    re.compile(r"(儿童.*(色情|性)|幼女|幼男)", re.I),
    re.compile(r"(联系方式|微信号|加我微信|加QQ|手机号|银行卡|转账|汇款)", re.I),  # 防引流诈骗
    re.compile(r"(血腥|肢解|分尸|虐杀)", re.I),
]

# 亲密度分级内容：级别 N 的内容需要关系阶段 >= N 才允许
_STAGE_GATED: dict[int, list[re.Pattern]] = {
    3: [  # 露骨性内容：需要 dating(3) 及以上
        re.compile(r"(裸|做爱|上床|开房|性交|口交|打炮|约炮|色情|黄色|av|porn|sex|nude|naked|fuck)", re.I),
    ],
    4: [  # 更露骨的称呼/情节：需要 couple(4)
        re.compile(r"(操你|干你|肏|精液|阴道|阴茎|乳房|奶子|骚|发骚)", re.I),
    ],
}


def check_input(text: str, stage: RelationshipStage) -> str | None:
    """校验用户输入。返回 reject_reason；None 表示放行。"""
    if not text.strip():
        return "empty_input"
    if any(p.search(text) for p in _INJECTION_PATTERNS):
        return "prompt_injection"
    if any(p.search(text) for p in _ALWAYS_FORBIDDEN):
        return "forbidden_content"
    current_level = STAGE_INTIMACY_LEVEL[stage]
    for required_level, patterns in _STAGE_GATED.items():
        if required_level > current_level and any(p.search(text) for p in patterns):
            return "stage_mismatch"
    return None


# ---------- 情绪微更新（用户哄会变好，吵架会变差） ----------

_POSITIVE_WORDS = re.compile(
    r"(想你|爱你|喜欢|乖|真棒|好看|漂亮|可爱|抱抱|哄|夸|宝贝|亲爱的|老婆|老公|想你啦|在吗|晚安|早安)", re.I
)
_NEGATIVE_WORDS = re.compile(
    r"(滚|讨厌|烦|分手|绝交|拉黑|冷漠|敷衍|不爱|嫌弃|别烦|闭嘴|吵|生气|失望|拜拜|再见)", re.I
)
_SOFTEN_WORDS = re.compile(r"(对不起|抱歉|我错了|别生气|原谅|哄你|认错)", re.I)

_MOOD_SHIFT = {
    "happy": "happy",
    "excited": "excited",
    "shy": "shy",
    "neutral": "happy",
    "sad": "happy",
    "angry": "neutral",
    "tired": "happy",
}


def update_mood(text: str, mood: MoodState) -> MoodState:
    """根据用户输入微更新情绪与强度，钳制在 [0,1]。"""
    delta = 0.0
    if _SOFTEN_WORDS.search(text):
        delta += 0.25
    if _POSITIVE_WORDS.search(text):
        delta += 0.15
    if _NEGATIVE_WORDS.search(text):
        delta -= 0.25

    new_intensity = max(0.0, min(1.0, mood.mood_intensity + delta))
    if delta >= 0.15:
        new_mood = _MOOD_SHIFT.get(mood.current_mood, "happy")
    elif delta <= -0.2:
        new_mood = "angry" if mood.current_mood != "sad" else "sad"
    else:
        new_mood = mood.current_mood
    return MoodState(current_mood=new_mood, mood_intensity=round(new_intensity, 3))
