"""内置 Skill 清单（注册中心初始内容）。

新增能力：加一个 Skill 子类实例 → 启动自动进注册中心；
第三方能力：`SKILL_PLUGIN_PACKAGES` 指定包名，包内暴露 `SKILLS = [Skill...]`。
"""
from __future__ import annotations

from app.skills.base import Skill
from app.skills.builtin.mood import MoodUpdateSkill
from app.skills.builtin.persona import PersonaNormalizeSkill
from app.skills.builtin.relationship import RelationshipUpdateSkill
from app.skills.builtin.reminder import ReminderCreateSkill
from app.skills.builtin.reply import ReplyPolishSkill
from app.skills.builtin.safety import SafetyGuardSkill
from app.skills.builtin.visual import ImageReplySkill, MemeReplySkill


def build_builtin_skills() -> list[Skill]:
    return [
        PersonaNormalizeSkill(),
        SafetyGuardSkill(),
        RelationshipUpdateSkill(),
        MoodUpdateSkill(),
        ReplyPolishSkill(),
                ReminderCreateSkill(),
        ImageReplySkill(),
        MemeReplySkill(),
    ]


__all__ = [
    "build_builtin_skills",
    "PersonaNormalizeSkill",
    "SafetyGuardSkill",
    "RelationshipUpdateSkill",
    "MoodUpdateSkill",
    "ReplyPolishSkill",
    "ReminderCreateSkill",
    "ImageReplySkill",
    "MemeReplySkill",
]
