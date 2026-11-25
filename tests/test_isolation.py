"""多 Agent 能力隔离：Skill 白名单 + MCP 白名单 + 共享能力 + 动态下线。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.agents.models import AgentProfile
from app.agents.registry import INTAKE_AGENT, REPLIER_AGENT, TOOLS_AGENT
from app.skills.base import Skill, SkillContext
from app.skills.models import SkillCall, SkillMeta
from tests.conftest import make_ctx


class _CalProbeInput(BaseModel):
    title: str = "探针提醒"


class _CalProbeSkill(Skill):
    """探针：调用日历 MCP，用来验证 MCP 层白名单。"""

    name = "test.cal_probe"
    version = "1.0.0"
    description = "探针：写一条日历提醒"
    Input = _CalProbeInput
    mcp_tools = ["calendar.create"]

    async def run(self, payload: _CalProbeInput, sctx: SkillContext) -> dict:
        remind_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = await sctx.call_mcp(
            "calendar.create",
            {"user_id": sctx.user_id, "title": payload.title, "remind_at": remind_at},
            declared=self.mcp_tools,
        )
        return {"reminder": result.data.get("reminder")}


async def test_replier_cannot_create_reminder():
    ctx = await make_ctx()
    sctx = ctx.skill_context("t-iso-1", "u1", REPLIER_AGENT)
    outcome = await ctx.executor.run(
        SkillCall(skill="reminder.create", args={"title": "x", "remind_at": "2030-01-01T00:00:00Z"}),
        sctx=sctx,
    )
    assert outcome.ok is False
    assert outcome.error_kind == "permission"
    events = [e for e in ctx.tracer_for("t-iso-1").tail(50) if e.get("action") == "skill.denied"]
    assert events, "越权必须留审计"


async def test_mcp_whitelist_blocks_undeclared_resource():
    ctx = await make_ctx()
    await ctx.skills.register(
        SkillMeta(
            name="test.cal_probe",
            class_path=f"{_CalProbeSkill.__module__}:{_CalProbeSkill.__qualname__}",
            description="探针",
            owner="test",
        )
    )
    no_calendar = AgentProfile(
        id="probe_no_cal", name="探针", description="", allowed_skills=["test.cal_probe"], allowed_mcp=[]
    )
    denied = await ctx.executor.run(
        SkillCall(skill="test.cal_probe", args={}), sctx=ctx.skill_context("t-iso-2", "u1", no_calendar)
    )
    assert denied.ok is False
    assert denied.error_kind == "permission"
    assert "calendar.create" in denied.message

    with_calendar = AgentProfile(
        id="probe_with_cal",
        name="探针",
        description="",
        allowed_skills=["test.cal_probe"],
        allowed_mcp=["calendar.*"],
    )
    allowed = await ctx.executor.run(
        SkillCall(skill="test.cal_probe", args={}), sctx=ctx.skill_context("t-iso-3", "u1", with_calendar)
    )
    assert allowed.ok is True
    assert allowed.data["reminder"]["title"] == "探针提醒"


async def test_shared_skill_available_to_two_agents():
    ctx = await make_ctx()
    for agent, tag in ((INTAKE_AGENT, "intake"), (REPLIER_AGENT, "replier")):
        sctx = ctx.skill_context(f"t-shared-{tag}", "u1", agent)
        outcome = await ctx.executor.run(
            SkillCall(skill="memory.recall", args={"query": "他喜欢什么"}), sctx=sctx
        )
        assert outcome.ok is True, f"{tag} 应能共享 memory.recall"


async def test_disabled_skill_cannot_be_called():
    ctx = await make_ctx()
    await ctx.skills.set_enabled("meme.reply", False)
    sctx = ctx.skill_context("t-off", "u1", TOOLS_AGENT)
    outcome = await ctx.executor.run(SkillCall(skill="meme.reply", args={}), sctx=sctx)
    assert outcome.ok is False
    assert outcome.error_kind == "business"
    assert "下线" in outcome.message

    await ctx.skills.set_enabled("meme.reply", True)
    assert (await ctx.skills.resolve("meme.reply")).name == "meme.reply"
