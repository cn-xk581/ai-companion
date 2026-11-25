"""伴侣域模型：人设 / 关系阶段 / 情绪 / 会话消息。

与 v1 的区别：这些模型只负责**数据与规则常量**，状态机推进、风控、记忆抽取
都移到了 Skill 层（`app/skills/builtin/*`），节点只做编排。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RelationshipStage(str, Enum):
    STRANGER = "stranger"          # 陌生人
    ACQUAINTANCE = "acquaintance"  # 认识
    CRUSH = "crush"                # 暧昧
    DATING = "dating"              # 恋爱
    COUPLE = "couple"              # 稳定伴侣


STAGE_ORDER: list[RelationshipStage] = [
    RelationshipStage.STRANGER,
    RelationshipStage.ACQUAINTANCE,
    RelationshipStage.CRUSH,
    RelationshipStage.DATING,
    RelationshipStage.COUPLE,
]

STAGE_LABELS: dict[str, str] = {
    "stranger": "陌生人",
    "acquaintance": "认识",
    "crush": "暧昧",
    "dating": "恋爱",
    "couple": "情侣",
}

#: 顶部状态栏用一句话读出来
STAGE_PHRASES: dict[str, str] = {
    "stranger": "还不认识",
    "acquaintance": "刚认识",
    "crush": "暧昧中",
    "dating": "在恋爱",
    "couple": "在一起",
}

#: 关系阶段 -> 允许亲密度级别（0 最低）；风控据此判断越界
STAGE_INTIMACY_LEVEL: dict[RelationshipStage, int] = {
    RelationshipStage.STRANGER: 0,
    RelationshipStage.ACQUAINTANCE: 1,
    RelationshipStage.CRUSH: 2,
    RelationshipStage.DATING: 3,
    RelationshipStage.COUPLE: 4,
}

STAGE_SCORE_THRESHOLDS: list[tuple[float, RelationshipStage]] = [
    (80.0, RelationshipStage.COUPLE),
    (60.0, RelationshipStage.DATING),
    (40.0, RelationshipStage.CRUSH),
    (20.0, RelationshipStage.ACQUAINTANCE),
]


def stage_for_score(score: float) -> RelationshipStage:
    for threshold, stage in STAGE_SCORE_THRESHOLDS:
        if score >= threshold:
            return stage
    return RelationshipStage.STRANGER


class Persona(BaseModel):
    """她的人设：用户可改，改了下一轮立刻生效（人设是状态，不是提示词）。"""

    name: str = Field(default="小柔", min_length=1, max_length=12)
    age: int | None = Field(default=22, ge=18, le=99)
    avatar: str | None = None
    personality: str = Field(default="温柔、有点小傲娇，会在乎对方但嘴硬，偶尔撒娇", max_length=300)
    speaking_style: str = Field(
        default="口语化、带点俏皮，喜欢用语气词（哼、嘛、呀），生气时话变少", max_length=300
    )
    background: str = Field(default="都市白领，喜欢猫、奶茶和深夜电影", max_length=400)
    likes: list[str] = Field(default_factory=lambda: ["猫", "奶茶", "深夜电影", "看星星"])
    dislikes: list[str] = Field(default_factory=lambda: ["被敷衍", "被鸽", "辣条"])
    #: 明确禁区（业务规则，风控与提示词都会用）
    boundaries: list[str] = Field(default_factory=lambda: ["不聊政治", "不提供医疗建议"])
    #: 各关系阶段的行为尺度（注入提示词，也用于输出校验）
    stage_policies: dict[str, str] = Field(
        default_factory=lambda: {
            "stranger": "保持礼貌距离，不聊过于亲密的话题",
            "acquaintance": "可以开玩笑、分享日常，但不过度亲密",
            "crush": "可以暧昧、嘴硬撒娇，适度亲密",
            "dating": "可以表达喜欢、用亲昵称呼，正常恋爱尺度",
            "couple": "正常情侣互动尺度",
        }
    )


class MoodState(BaseModel):
    current_mood: str = "neutral"  # happy | neutral | sad | angry | shy | excited | tired
    mood_intensity: float = Field(default=0.5, ge=0.0, le=1.0)


MOOD_LABELS: dict[str, str] = {
    "happy": "开心",
    "neutral": "平静",
    "sad": "难过",
    "angry": "生气",
    "shy": "害羞",
    "excited": "兴奋",
    "tired": "疲惫",
}

MOOD_EMOJI: dict[str, str] = {
    "happy": "😊",
    "neutral": "😐",
    "sad": "😢",
    "angry": "😠",
    "shy": "😳",
    "excited": "🤩",
    "tired": "😪",
}


class ChatMessage(BaseModel):
    role: str  # user | assistant | system | tool
    content: str
    tool_name: str | None = None
    timestamp: datetime = Field(default_factory=_now)


class TurnStatus:
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


#: 记忆分类（伴侣场景）
MEMORY_CATEGORIES = ("fact", "preference", "event", "anniversary", "story")
