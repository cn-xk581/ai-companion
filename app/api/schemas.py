"""HTTP 契约：伴侣对话 / 会话 / 人设 / 记忆 / 轮次审计 / 运维。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.companion import MoodState, Persona


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    user_input: str = Field(min_length=1, max_length=2000)
    client_msg_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    turn_id: str
    session_id: str
    status: str
    reply: str = ""
    stage: str | None = None
    stage_label: str | None = None
    score: float | None = None
    mood: str | None = None
    mood_label: str | None = None
    warnings: list[str] = Field(default_factory=list)
    interrupt: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class SessionCreateRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)


class SessionDetailResponse(BaseModel):
    session_id: str
    persona: Persona
    stage: str
    stage_label: str
    score: float
    mood: MoodState
    mood_label: str


class SessionListItem(BaseModel):
    session_id: str
    name: str
    avatar: str | None = None
    stage: str
    stage_label: str
    score: float
    mood: str
    mood_label: str
    last_message: str = ""
    last_time: Any | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class HistoryMessage(BaseModel):
    role: str
    content: str
    tool_name: str | None = None
    timestamp: Any | None = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]


class ResetRequest(BaseModel):
    clear_memories: bool = True


class ResetResponse(BaseModel):
    session_id: str
    reset: bool = True
    cleared_memories: bool = False


class MemoryItemOut(BaseModel):
    id: str
    content: str
    importance: float
    category: str
    tags: list[str] = Field(default_factory=list)
    created_at: Any | None = None


class MemoryListResponse(BaseModel):
    user_id: int
    memories: list[MemoryItemOut]


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class ResumeRequest(BaseModel):
    decision: Literal["approve", "reject"]
    feedback: str = Field(default="", max_length=300)
    by: str = Field(default="operator", max_length=64)


class TurnResponse(BaseModel):
    turn_id: str
    session_id: str
    status: str
    result: dict[str, Any] | None = None
    interrupt: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class TraceResponse(BaseModel):
    turn_id: str
    count: int
    events: list[dict[str, Any]]


class SkillRegisterRequest(BaseModel):
    name: str = Field(min_length=3, max_length=64)
    class_path: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=300)
    owner: str = Field(default="external", max_length=64)
    enabled: bool = True


class SkillActionResponse(BaseModel):
    skill: str
    enabled: bool | None = None
    registered: bool | None = None
    deleted: bool | None = None
    meta: dict[str, Any] | None = None


class SkillListResponse(BaseModel):
    skills: list[dict[str, Any]]
    stats: dict[str, Any]


class AgentListResponse(BaseModel):
    agents: list[dict[str, Any]]


class MCPListResponse(BaseModel):
    tools: list[dict[str, Any]]
    health: dict[str, Any]


class GraphResponse(BaseModel):
    nodes: list[dict[str, str]]
    edges: list[list[str]]


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    domain: str
    llm: dict[str, Any] = Field(default_factory=dict)
    checkpoint: dict[str, Any]
    skills: dict[str, Any]
    mcp: dict[str, Any]
    singleflight: dict[str, Any]
