"""Agent 定义：一个 Agent = 一组 Skill 白名单 + 一组 MCP 资源白名单 + 预算。

为什么要白名单（而不是「所有 Agent 共享全部能力」）
---------------------------------------------------
1. **越权隔离**：回复 Agent 不该有「建提醒」「写记忆」的能力，也不该读到别的会话；
2. **上下文收窄**：给 LLM 的候选 Skill 越少，选错/幻觉出不存在工具的概率越低；
3. **爆炸半径**：某个 Skill 被恶意注册时，只有白名单里含它的 Agent 会受影响；
4. **成本**：每个 Agent 有独立的轮次/调用预算，避免一个 Agent 吃掉整个任务配额。

注意 `allowed_mcp`：即使某个 Skill 在白名单里，它访问外部资源仍要过 Agent 的 MCP 白名单，
防止「用合法 Skill 夹带未授权的数据源」。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AgentProfile(BaseModel):
    id: str
    name: str
    description: str
    #: Skill 白名单，支持 `memory.*` 前缀通配
    allowed_skills: list[str] = Field(default_factory=list)
    #: MCP 工具白名单，支持 `nutrition.*` 前缀通配
    allowed_mcp: list[str] = Field(default_factory=list)
    #: 单次任务内该 Agent 的调用预算
    max_skill_calls: int = 6
    max_llm_calls: int = 3
    temperature: float = 0.2
    max_output_tokens: int = 900

    def can_use_skill(self, name: str) -> bool:
        from app.mcp.models import match_allow

        return match_allow(name, self.allowed_skills)

    def can_use_mcp(self, tool: str) -> bool:
        from app.mcp.models import match_allow

        return match_allow(tool, self.allowed_mcp)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "allowed_skills": self.allowed_skills,
            "allowed_mcp": self.allowed_mcp,
            "max_skill_calls": self.max_skill_calls,
            "max_llm_calls": self.max_llm_calls,
        }
