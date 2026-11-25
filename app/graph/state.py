"""一轮对话的图状态（TypedDict，全部可 JSON 化，逐节点存检查点）。

会话级的持久状态（人设/阶段/分数/情绪）由 service 从仓库加载后放进 state，
节点只读它、产出**增量**写回，最后 persist 统一落库 —— 避免节点各自写库导致不一致。
"""
from __future__ import annotations

from typing import Any, TypedDict

from app.domain.companion import TurnStatus

STATUS_RUNNING = TurnStatus.RUNNING
STATUS_AWAITING_REVIEW = TurnStatus.AWAITING_REVIEW
STATUS_DONE = TurnStatus.DONE
STATUS_BLOCKED = TurnStatus.BLOCKED
STATUS_FAILED = TurnStatus.FAILED


class CompanionState(TypedDict, total=False):
    # ---- 身份 ----
    turn_id: str
    session_id: str
    user_id: str
    started_at: float

    # ---- 输入 ----
    user_input: str
    client_msg_id: str | None

    # ---- 会话状态（进入时快照，persist 时写回） ----
    persona: dict[str, Any]
    stage: str
    score: float
    mood: dict[str, Any]

    # ---- 上下文 ----
    memories: list[str]
    history: list[dict[str, str]]

    # ---- 运行态 ----
    safety: dict[str, Any] | None      # 输入风控
    plan: dict[str, Any] | None
    calls: list[dict[str, Any]]
    outcomes: list[dict[str, Any]]
    tool_text: str
    draft: str | None
    reply: str | None
    review: dict[str, Any] | None      # 输出风控
    human: dict[str, Any] | None
    status: str
    guard: dict[str, Any]

    # ---- 记账 ----
    llm_calls: int
    skill_calls: int
    errors: list[dict[str, Any]]
    warnings: list[str]

    # ---- 结果 ----
    result: dict[str, Any] | None
    trace_summary: dict[str, Any]
