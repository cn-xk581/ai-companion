"""人设、关系阶段、情绪状态模型。

核心思想：关系阶段/情绪不是塞在 Prompt 里的字符串，而是持久化状态，
由 LangGraph State 携带，从根源上解决人设漂移。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RelationshipStage(str, Enum):
    STRANGER = "stranger"          # 陌生人
    ACQUAINTANCE = "acquaintance"  # 认识
    CRUSH = "crush"                # 暧昧
    DATING = "dating"              # 约会/恋爱
    COUPLE = "couple"              # 稳定情侣


# 关系阶段 -> 允许的亲密度级别（0 最低）。state_guard 用它校验输入尺度。
STAGE_INTIMACY_LEVEL: dict[RelationshipStage, int] = {
    RelationshipStage.STRANGER: 0,
    RelationshipStage.ACQUAINTANCE: 1,
    RelationshipStage.CRUSH: 2,
    RelationshipStage.DATING: 3,
    RelationshipStage.COUPLE: 4,
}

# 关系分数阈值 -> 阶段（relationship_score 0~100）
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
    """女友人设，可运行时通过 /persona/update 修改并持久化。"""

    name: str = "小柔"
    age: int | None = 22
    avatar: str | None = None
    personality: str = "温柔、有点小傲娇，会在乎对方但嘴硬，偶尔撒娇"
    speaking_style: str = "口语化、带点俏皮，喜欢用语气词（哼、嘛、呀），生气时话变少"
    background: str = "都市白领，喜欢猫、奶茶和深夜电影"
    likes: list[str] = Field(default_factory=lambda: ["猫", "奶茶", "深夜电影", "看星星"])
    dislikes: list[str] = Field(default_factory=lambda: ["被敷衍", "被鸽", "辣条"])
    # 每个关系阶段对应的行为尺度约束（注入 Prompt）
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
    """当前情绪（每轮由 state_guard 微更新，随对话起伏）。"""

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
