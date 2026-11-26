"""羁绊记忆领域模型：关系 / 事实 / 情景 / 会话摘要。"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 事实状态
FACT_PENDING = 0
FACT_ACTIVE = 1
FACT_SUPERSEDED = 2
FACT_ARCHIVED = 3

# 情景状态
EP_ACTIVE = 1
EP_ARCHIVED = 3

FACT_CATEGORIES = ("profile", "preference", "social", "taboo")
EPISODE_KINDS = ("event", "milestone", "promise", "conflict", "thread", "memoir")


def now() -> datetime:
    return datetime.now(timezone.utc)


def fact_key_norm(category: str, key: str) -> str:
    raw = f"{category.strip().lower()}.{key.strip().lower()}"
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in raw)
    return cleaned[:64] or "auto." + hashlib.sha1(raw.encode()).hexdigest()[:12]


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:32]


@dataclass
class Relation:
    id: int
    user_id: int
    character_id: int
    user_persona_override: str = ""
    user_nickname: str = ""
    affinity_score: int = 0
    relationship_stage: str = "stranger"
    current_mood: str = "neutral"
    total_turns: int = 0
    last_interaction_at: datetime | None = None
    last_consolidated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Fact:
    id: int
    relation_id: int
    category: str
    fact_key: str
    fact_key_norm: str
    fact_value: str
    confidence: float = 1.0
    is_pinned: bool = False
    status: int = FACT_ACTIVE
    superseded_by: int | None = None
    mention_count: int = 1
    content_hash: str = ""
    source_msg_id: int | None = None
    last_confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Episode:
    id: int
    relation_id: int
    session_id: str = ""
    kind: str = "event"
    summary: str = ""
    emotion_tag: str = ""
    importance: int = 3
    status: int = EP_ACTIVE
    is_pinned: bool = False
    happened_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0
    embedding: list[float] = field(default_factory=list)
    source_msg_id: int | None = None
    created_at: datetime | None = None


@dataclass
class SessionSummary:
    session_id: str
    summary: str
    covered_until_msg_id: int | None = None
    covered_turns: int = 0
    token_estimate: int = 0
    updated_at: datetime | None = None


def half_life_hours(importance: int, pinned: bool = False) -> float:
    if pinned or importance >= 9:
        return float("inf")
    if importance >= 7:
        return 90 * 24
    if importance >= 4:
        return 14 * 24
    return 3 * 24


def retention_score(
    *,
    importance: int,
    pinned: bool,
    last_accessed_at: datetime | None,
    happened_at: datetime | None,
    relevance: float = 0.0,
    now_ts: datetime | None = None,
) -> float:
    """0.4*recency + 0.3*importance + 0.3*relevance；pinned 不衰减。"""
    current = now_ts or now()
    reference = last_accessed_at or happened_at or current
    delta_h = max(0.0, (current - reference).total_seconds() / 3600.0)
    half = half_life_hours(importance, pinned)
    if math.isinf(half):
        recency = 1.0
    else:
        recency = math.exp(-math.log(2) * delta_h / half)
    return 0.4 * recency + 0.3 * max(0, min(importance, 10)) / 10 + 0.3 * max(0.0, min(relevance, 1.0))


def summary_line(fact: "Fact") -> str:
    return f"{fact.fact_value}"


__all__ = [
    "FACT_PENDING",
    "FACT_ACTIVE",
    "FACT_SUPERSEDED",
    "FACT_ARCHIVED",
    "EP_ACTIVE",
    "EP_ARCHIVED",
    "FACT_CATEGORIES",
    "EPISODE_KINDS",
    "Relation",
    "Fact",
    "Episode",
    "SessionSummary",
    "fact_key_norm",
    "content_hash",
    "retention_score",
    "half_life_hours",
]
