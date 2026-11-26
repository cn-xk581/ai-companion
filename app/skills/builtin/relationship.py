"""Skill: relationship.update —— 关系分数与阶段跃迁（纯业务规则）。

设计要点
--------
1. 分数变化有**上限**：单轮 ±2.5 以内，避免一句话把关系刷到情侣；
2. **单轮最多升/降一级**：防止「一次表白直接跳过暧昧」这种不真实跃迁；
3. 变化必须有原因（reasons），写进审计，方便解释「为什么她突然冷淡了」。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.companion import (
    STAGE_LABELS,
    STAGE_ORDER,
    STAGE_SCORE_THRESHOLDS,
    RelationshipStage,
    stage_for_score,
)
from app.skills.base import Skill, SkillContext

TURN_DELTA_MIN = -2.5
TURN_DELTA_MAX = 2.0


class Signals(BaseModel):
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    safety_action: Literal["allow", "soft", "block", "review"] = "allow"
    used_tool: bool = False
    intimacy_level: int = Field(default=0, ge=0, le=4)
    repeat_count: int = 0  # 最近重复同一句话的次数（防刷）


class RelationshipInput(BaseModel):
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    stage: str = "stranger"
    signals: Signals = Field(default_factory=Signals)


class RelationshipOutput(BaseModel):
    score: float
    stage: str
    stage_label: str
    delta: float
    intimacy_level: int
    stage_changed: bool
    reasons: list[str] = Field(default_factory=list)


def _stage(value: str) -> RelationshipStage:
    try:
        return RelationshipStage(value)
    except ValueError:
        return RelationshipStage.STRANGER


def _floor_for(stage: RelationshipStage) -> float:
    for threshold, candidate in STAGE_SCORE_THRESHOLDS:
        if candidate == stage:
            return threshold
    return 0.0


class RelationshipUpdateSkill(Skill):
    name = "relationship.update"
    version = "1.1.0"
    description = "按本轮信号推进关系分数与阶段（有单轮上限、最多跃迁一级）"
    owner = "companion-core"
    tags = ["business", "state"]
    Input = RelationshipInput
    Output = RelationshipOutput
    mcp_tools = []
    timeout_sec = 2.0

    async def run(self, payload: RelationshipInput, sctx: SkillContext) -> RelationshipOutput:
        old_stage = _stage(payload.stage)
        reasons: list[str] = []
        delta = 0.0

        signals = payload.signals
        if signals.safety_action == "allow":
            delta += 0.6
            reasons.append("正常互动 +0.6")
        if signals.used_tool:
            delta += 0.3
            reasons.append("她帮上了忙 +0.3")
        if signals.sentiment == "positive":
            delta += 0.6
            reasons.append("你表达了正向情绪 +0.6")
        elif signals.sentiment == "negative":
            delta -= 1.2
            reasons.append("出现冲突/负面情绪 -1.2")
        if signals.safety_action == "soft":
            delta -= 0.8
            reasons.append("轻微越界 -0.8")
        elif signals.safety_action == "block":
            delta -= 2.0
            reasons.append("明确越界 -2.0")
        elif signals.safety_action == "review":
            delta -= 0.3
            reasons.append("内容需人工复核 -0.3")

        # 防刷：重复发同一句话不涨，连续重复还要扣
        if signals.repeat_count > 0:
            penalty = min(1.5, 0.4 * signals.repeat_count)
            delta = min(delta, 0.0) - penalty
            reasons.append(f"重复发送同一句话 ×{signals.repeat_count}，-{penalty:.1f}")

        delta = max(TURN_DELTA_MIN, min(TURN_DELTA_MAX, delta))
        new_score = max(0.0, min(100.0, payload.score + delta))
        new_stage = stage_for_score(new_score)

        # 单轮最多跃迁一级（升/降都限制），把分数钳回阈值附近
        old_idx = STAGE_ORDER.index(old_stage)
        new_idx = STAGE_ORDER.index(new_stage)
        if new_idx > old_idx + 1:
            new_stage = STAGE_ORDER[old_idx + 1]
            new_score = min(new_score, _floor_for(new_stage) + 0.1)
            reasons.append(f"单轮最多升一级，钳制在{STAGE_LABELS[new_stage.value]}")
        elif new_idx < old_idx - 1:
            new_stage = STAGE_ORDER[old_idx - 1]
            new_score = max(new_score, _floor_for(new_stage))
            reasons.append(f"单轮最多降一级，钳制在{STAGE_LABELS[new_stage.value]}")

        stage_changed = new_stage != old_stage
        if stage_changed:
            reasons.append(
                f"阶段 {STAGE_LABELS[old_stage.value]} → {STAGE_LABELS[new_stage.value]}"
            )

        await sctx.tracer.decision(
            "relationship.updated",
            score=round(new_score, 2),
            stage=new_stage.value,
            delta=round(delta, 2),
            reasons=reasons,
        )
        return RelationshipOutput(
            score=round(new_score, 2),
            stage=new_stage.value,
            stage_label=STAGE_LABELS[new_stage.value],
            delta=round(delta, 2),
            intimacy_level=new_idx,
            stage_changed=stage_changed,
            reasons=reasons,
        )
