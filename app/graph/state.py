"""LangGraph 全局状态：关系阶段、情绪、记忆、对话上下文全部放进 AgentState。

说明：这里刻意用 Pydantic 普通 list 字段（节点内读改写），而不是 Annotated + operator.add
reducer —— 本图是顺序执行链路，无需并发合并，普通字段跨 langgraph 版本最稳。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.persona.models import MoodState, Persona, RelationshipStage


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChatMessage(BaseModel):
    role: str  # user | assistant | system | tool
    content: str
    tool_name: str | None = None
    timestamp: datetime = Field(default_factory=_now)


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    category: str = "fact"  # fact | preference | event | anniversary | story
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    name: str
    output: str
    success: bool = True


class AgentState(BaseModel):
    """整张图的运行时状态。节点函数输入它、返回部分更新。"""

    # ---- 身份 ----
    session_id: str
    user_id: str = "default"
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    # ---- 本轮输入 ----
    user_input: str = ""

    # ---- 幂等 ----
    client_msg_id: str | None = None  # 客户端消息幂等键：重试同一消息复用同一 id，避免重复落库

    # ---- 人设 & 关系 & 情绪（持久化状态）----
    persona: Persona = Field(default_factory=Persona)
    relationship_stage: RelationshipStage = RelationshipStage.STRANGER
    relationship_score: float = Field(default=0.0, ge=0.0, le=100.0)
    mood: MoodState = Field(default_factory=MoodState)

    # ---- 上下文 ----
    chat_history: list[ChatMessage] = Field(default_factory=list)  # 滑动窗口短期历史
    retrieved_memories: list[MemoryItem] = Field(default_factory=list)  # 相关长期记忆
    new_memories: list[MemoryItem] = Field(default_factory=list)  # 本轮抽取待写入

    # ---- 规划 & 工具 ----
    need_tool_call: bool = False
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)

    # ---- 输出 ----
    agent_reply: str | None = None
    reject_reason: str | None = None  # prompt_injection | stage_mismatch | forbidden_content ...

    # ---- 元信息 ----
    guard_flags: dict = Field(default_factory=dict)
