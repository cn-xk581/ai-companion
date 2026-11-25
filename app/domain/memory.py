"""长期记忆的领域模型（跨会话，scope 用 user_id）。

她记得的是「关于你的事实」：喜好、忌口、纪念日、发生过的事、你说过的话。
只做偏好沉淀，不参与关系/情绪判定，也不允许覆盖用户当轮的显式表达。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    category: str = "fact"  # fact | preference | event | anniversary | story
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
