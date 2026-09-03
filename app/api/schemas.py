"""HTTP 请求/响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.graph.state import ChatMessage, MemoryItem
from app.persona.models import MoodState, Persona


class ChatRequest(BaseModel):
    session_id: str | None = None
    user_input: str = Field(min_length=1, max_length=2000)
    client_msg_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    relationship_stage: str
    relationship_score: float
    mood: str
    mood_intensity: float
    tool_results: list[str] = Field(default_factory=list)
    reject_reason: str | None = None
    trace_id: str | None = None


class SessionResetRequest(BaseModel):
    session_id: str


class SessionResetResponse(BaseModel):
    session_id: str
    reset: bool = True


class SessionListItem(BaseModel):
    session_id: str
    name: str
    avatar: str | None = None
    relationship_stage: str
    relationship_score: float
    mood: str
    mood_intensity: float
    last_message: str | None = None
    last_time: datetime | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage] = Field(default_factory=list)


class SessionDetailResponse(BaseModel):
    session_id: str
    persona: Persona
    relationship_stage: str
    relationship_score: float
    mood: MoodState


class MemoryListResponse(BaseModel):
    session_id: str
    memories: list[MemoryItem] = Field(default_factory=list)


class MemoryDeleteRequest(BaseModel):
    session_id: str
    memory_id: str


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class PersonaUpdateRequest(BaseModel):
    session_id: str
    persona: Persona


class PersonaUpdateResponse(BaseModel):
    session_id: str
    persona: Persona
