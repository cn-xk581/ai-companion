"""Skill 注册中心：内置同步、动态上下线、动态注册（以代码为准）、注销。"""
from __future__ import annotations

from pydantic import BaseModel

from app.skills.base import Skill, SkillContext
from app.skills.models import SkillMeta
from tests.conftest import make_ctx


class _EchoInput(BaseModel):
    text: str = "hi"


class _EchoOutput(BaseModel):
    text: str


class EchoSkill(Skill):
    name = "test.echo"
    version = "2.1.0"
    description = "原样返回"
    Input = _EchoInput
    Output = _EchoOutput

    async def run(self, payload: _EchoInput, sctx: SkillContext) -> _EchoOutput:
        return _EchoOutput(text=payload.text)


async def test_bootstrap_registers_companion_skills():
    ctx = await make_ctx()
    metas = {m["name"]: m for m in await ctx.skills.list_public()}
    for name in ("persona.normalize", "safety.guard", "relationship.update", "mood.update",
                 "reply.polish", "memory.recall", "memory.remember", "reminder.create",
                 "image.reply", "meme.reply"):
        assert name in metas, f"缺少内置 Skill：{name}"
    assert metas["reminder.create"]["mcp_tools"] == ["calendar.create", "calendar.list"]
    assert metas["persona.normalize"]["input_schema"]["properties"]
    stats = await ctx.skills.stats()
    assert stats["total"] >= 10


async def test_disable_then_enable_without_restart():
    ctx = await make_ctx()
    await ctx.skills.set_enabled("image.reply", False)
    assert await _enabled(ctx, "image.reply") is False
    await ctx.skills.set_enabled("image.reply", True)
    assert await _enabled(ctx, "image.reply") is True


async def test_dynamic_register_uses_code_schema_not_posted_schema():
    ctx = await make_ctx()
    meta = await ctx.skills.register(
        SkillMeta(
            name="test.echo",
            class_path=f"{EchoSkill.__module__}:{EchoSkill.__qualname__}",
            description="动态注册",
            input_schema={"properties": {"fake": {"type": "string"}}},
        )
    )
    assert "text" in meta.input_schema["properties"]
    assert "fake" not in meta.input_schema["properties"]
    assert (await ctx.skills.resolve("test.echo")).version == "2.1.0"


async def test_unregister_removes_skill():
    ctx = await make_ctx()
    await ctx.skills.register(
        SkillMeta(name="test.echo", class_path=f"{EchoSkill.__module__}:{EchoSkill.__qualname__}")
    )
    assert await ctx.skills.unregister("test.echo") is True
    from app.core.errors import NotFoundError

    try:
        await ctx.skills.resolve("test.echo")
    except NotFoundError:
        return
    raise AssertionError("注销后不应还能解析到")


async def test_register_rejects_bad_class_path():
    ctx = await make_ctx()
    from app.core.errors import ValidationFailed

    try:
        await ctx.skills.register(SkillMeta(name="test.broken", class_path="not_a_module:NoSuchSkill"))
    except ValidationFailed:
        return
    raise AssertionError("非法 class_path 必须拒绝")


async def _enabled(ctx, name: str) -> bool:
    for meta in await ctx.skills.list_public():
        if meta["name"] == name:
            return bool(meta["enabled"])
    raise AssertionError(f"{name} 不在注册中心")
