"""内置 Agent 注册表：四个角色 + 白名单，覆盖「接入 → 回复 → 工具 → 状态推进」。

| Agent | 能用的 Skill | 能访问的 MCP | 不能做的（会被拒） |
|---|---|---|---|
| companion_intake | persona.normalize / safety.guard | 无 | 生成回复、调提醒 |
| companion_replier | reply.polish | 无 | 建提醒、画图（没有对应 MCP） |
| companion_tools | reminder.create / image.reply / meme.reply | calendar.* / image.* / meme.* | 改关系阶段、写记忆 |
| companion_state | mood.update / relationship.update | 无 | 调用任何外部工具 |

`reminder.create` 只给 tools —— 即使 replier 想偷偷建提醒也会被白名单拒绝。
"""
from __future__ import annotations

from app.agents.models import AgentProfile
from app.core.errors import NotFoundError

INTAKE_AGENT = AgentProfile(
    id="companion_intake",
    name="接入 Agent",
    description="校验人设、做输入风控、召回长期记忆",
    allowed_skills=["persona.normalize", "safety.guard"],
    allowed_mcp=[],
    max_skill_calls=4,
    max_llm_calls=0,
    temperature=0.0,
)

REPLIER_AGENT = AgentProfile(
    id="companion_replier",
    name="回复 Agent",
    description="组织上下文、生成回复、按阶段润色（不碰外部工具）",
    allowed_skills=["reply.polish"],
    allowed_mcp=[],
    max_skill_calls=4,
    max_llm_calls=2,
    temperature=0.85,
    max_output_tokens=600,
)

TOOLS_AGENT = AgentProfile(
    id="companion_tools",
    name="工具 Agent",
    description="提醒、画图、表情：需要外部资源的能力都归它",
    allowed_skills=["reminder.create", "image.reply", "meme.reply"],
    allowed_mcp=["calendar.*", "image.*", "meme.*"],
    max_skill_calls=3,
    max_llm_calls=1,
    temperature=0.3,
    max_output_tokens=400,
)

STATE_AGENT = AgentProfile(
    id="companion_state",
    name="状态 Agent",
    description="推进情绪与关系、沉淀长期记忆",
    allowed_skills=["mood.update", "relationship.update"],
    allowed_mcp=[],
    max_skill_calls=4,
    max_llm_calls=0,
    temperature=0.0,
)

_AGENTS: dict[str, AgentProfile] = {
    a.id: a for a in (INTAKE_AGENT, REPLIER_AGENT, TOOLS_AGENT, STATE_AGENT)
}


def get_agent(agent_id: str) -> AgentProfile:
    agent = _AGENTS.get(agent_id)
    if agent is None:
        raise NotFoundError(f"未知 Agent：{agent_id}", detail={"agent": agent_id})
    return agent


def list_agents() -> list[AgentProfile]:
    return list(_AGENTS.values())


__all__ = [
    "AgentProfile",
    "INTAKE_AGENT",
    "REPLIER_AGENT",
    "TOOLS_AGENT",
    "STATE_AGENT",
    "get_agent",
    "list_agents",
]
