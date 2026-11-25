"""Skill: persona.normalize —— 人设归一化与合法性校验。

人设是**持久化状态**，不是提示词里的装饰：她叫什么、尺度到哪、什么不能说，
都必须先过这里，后面的风控与提示词才有统一依据。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.errors import ValidationFailed
from app.domain.companion import STAGE_ORDER, Persona
from app.skills.base import Skill, SkillContext


class PersonaInput(BaseModel):
    persona: dict = Field(default_factory=dict)


class PersonaOutput(BaseModel):
    persona: Persona
    warnings: list[str] = Field(default_factory=list)


class PersonaNormalizeSkill(Skill):
    name = "persona.normalize"
    version = "1.1.0"
    description = "校验并归一化人设（名字/年龄/性格/尺度策略/禁区），产出可直接使用的 Persona"
    owner = "companion-core"
    tags = ["business", "persona"]
    Input = PersonaInput
    Output = PersonaOutput
    mcp_tools = []
    timeout_sec = 2.0

    async def run(self, payload: PersonaInput, sctx: SkillContext) -> PersonaOutput:
        warnings: list[str] = []
        raw = dict(payload.persona or {})

        # 只接收已知字段，避免脏字段进入后续提示词
        allowed = set(Persona.model_fields.keys())
        unknown = [k for k in raw if k not in allowed]
        if unknown:
            warnings.append("忽略未知人设字段：" + "、".join(sorted(unknown)[:6]))
        data = {k: v for k, v in raw.items() if k in allowed}

        if "name" in data:
            data["name"] = str(data["name"]).strip()
            if not data["name"]:
                raise ValidationFailed("人设名字不能为空", user_hint="给她起个名字吧")

        for field in ("personality", "speaking_style", "background"):
            if field in data and isinstance(data[field], str):
                data[field] = data[field].strip()

        for field in ("likes", "dislikes"):
            if field in data and isinstance(data[field], list):
                cleaned: list[str] = []
                for item in data[field][:10]:
                    text = str(item).strip()[:20]
                    if text and text not in cleaned:
                        cleaned.append(text)
                data[field] = cleaned

        if "boundaries" in data and isinstance(data["boundaries"], list):
            bounds: list[str] = []
            for item in data["boundaries"][:10]:
                text = str(item).strip()[:40]
                if text and text not in bounds:
                    bounds.append(text)
            data["boundaries"] = bounds

        # 尺度策略必须覆盖全部关系阶段，缺的补默认
        policies = dict(data.get("stage_policies") or {})
        default = Persona().stage_policies
        for stage in STAGE_ORDER:
            policies.setdefault(stage.value, default[stage.value])
        data["stage_policies"] = {k: str(v)[:80] for k, v in policies.items()}

        try:
            persona = Persona.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            raise ValidationFailed(f"人设字段不合法：{exc}", user_hint="检查一下名字和年龄") from exc

        await sctx.tracer.decision(
            "persona.normalized", name=persona.name, age=persona.age, boundaries=len(persona.boundaries)
        )
        return PersonaOutput(persona=persona, warnings=warnings)
