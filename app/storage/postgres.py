"""持久化仓库：PostgreSQL(async) + 内存降级。

五张表
------
| 表 | 用途 | 关键点 |
|---|---|---|
| users | JWT 用户 | 用户名唯一 |
| sessions | 人设 / 关系阶段 / 分数 / 情绪 | upsert，会话级快照 |
| messages | 对话历史 | 按 session 追加，取最近 N 条 |
| memories | 长期记忆（scope=user_id） | importance 排序，跨会话 |
| turns | 每一轮的运行结果与状态 | 支持人工复核后查询 |
| turn_events | 全链路审计事件 | 只追加，按 turn_id 查 |
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.companion import ChatMessage, MoodState, Persona, RelationshipStage
from app.domain.memory import MemoryItem

logger = logging.getLogger(__name__)


@dataclass
class UserRecord:
    id: str
    username: str
    password_hash: str
    created_at: datetime | None = None


@dataclass
class SessionRecord:
    session_id: str
    user_id: str = "default"
    persona: Persona = field(default_factory=Persona)
    stage: RelationshipStage = RelationshipStage.STRANGER
    score: float = 0.0
    mood: MoodState = field(default_factory=MoodState)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DuplicateUserError(Exception):
    pass


class Repository(ABC):
    # ---- 用户 ----
    @abstractmethod
    async def create_user(self, username: str, password_hash: str) -> UserRecord: ...

    @abstractmethod
    async def get_user_by_username(self, username: str) -> UserRecord | None: ...

    @abstractmethod
    async def get_user(self, user_id: str) -> UserRecord | None: ...

    # ---- 会话 ----
    @abstractmethod
    async def get_session(self, session_id: str) -> SessionRecord | None: ...

    @abstractmethod
    async def save_session(self, record: SessionRecord) -> None: ...

    @abstractmethod
    async def list_sessions_by_user(self, user_id: str) -> list[SessionRecord]: ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> None: ...

    # ---- 消息 ----
    @abstractmethod
    async def append_message(self, session_id: str, message: ChatMessage) -> None: ...

    @abstractmethod
    async def recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]: ...

    @abstractmethod
    async def clear_messages(self, session_id: str) -> None: ...

    # ---- 记忆 ----
    @abstractmethod
    async def save_memory(self, user_id: str, memory: MemoryItem) -> None: ...

    @abstractmethod
    async def list_memories(self, user_id: str) -> list[MemoryItem]: ...

    @abstractmethod
    async def delete_memory(self, user_id: str, memory_id: str) -> bool: ...

    @abstractmethod
    async def clear_memories(self, user_id: str) -> int: ...

    # ---- 轮次 ----
    @abstractmethod
    async def save_turn(
        self, turn_id: str, session_id: str, user_id: str, status: str, payload: dict[str, Any]
    ) -> None: ...

    @abstractmethod
    async def get_turn(self, turn_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list_turns(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]: ...

    # ---- 审计事件 ----
    @abstractmethod
    async def append_event(self, turn_id: str, event: dict[str, Any]) -> None: ...

    @abstractmethod
    async def list_events(
        self, turn_id: str, limit: int = 500, after_id: int = 0
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def close(self) -> None: ...


# ================= 内存实现 =================


class InMemoryRepository(Repository):
    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._usernames: dict[str, str] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[ChatMessage]] = {}
        self._memories: dict[str, dict[str, MemoryItem]] = {}
        self._turns: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._event_seq = 0

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        if username in self._usernames:
            raise DuplicateUserError(username)
        user = UserRecord(id=uuid.uuid4().hex, username=username, password_hash=password_hash)
        self._users[user.id] = user
        self._usernames[username] = user.id
        return user

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        uid = self._usernames.get(username)
        return self._users.get(uid) if uid else None

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self._users.get(user_id)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def save_session(self, record: SessionRecord) -> None:
        now = datetime.now(timezone.utc)
        existing = self._sessions.get(record.session_id)
        record.created_at = record.created_at or (existing.created_at if existing else now)
        record.updated_at = now
        self._sessions[record.session_id] = record

    async def list_sessions_by_user(self, user_id: str) -> list[SessionRecord]:
        rows = [s for s in self._sessions.values() if s.user_id == user_id]
        rows.sort(key=lambda s: s.updated_at or datetime.now(timezone.utc), reverse=True)
        return rows

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._messages.pop(session_id, None)

    async def append_message(self, session_id: str, message: ChatMessage) -> None:
        self._messages.setdefault(session_id, []).append(message)

    async def recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]:
        return self._messages.get(session_id, [])[-limit:]

    async def clear_messages(self, session_id: str) -> None:
        self._messages.pop(session_id, None)

    async def save_memory(self, user_id: str, memory: MemoryItem) -> None:
        self._memories.setdefault(user_id, {})[memory.id] = memory

    async def list_memories(self, user_id: str) -> list[MemoryItem]:
        rows = self._memories.get(user_id, {}).values()
        return sorted(rows, key=lambda m: m.importance, reverse=True)

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        return self._memories.get(user_id, {}).pop(memory_id, None) is not None

    async def clear_memories(self, user_id: str) -> int:
        count = len(self._memories.get(user_id, {}))
        self._memories.pop(user_id, None)
        return count

    async def save_turn(
        self, turn_id: str, session_id: str, user_id: str, status: str, payload: dict[str, Any]
    ) -> None:
        now = datetime.now(timezone.utc)
        existing = self._turns.get(turn_id)
        self._turns[turn_id] = {
            "turn_id": turn_id,
            "session_id": session_id,
            "user_id": user_id,
            "status": status,
            "payload": payload,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }

    async def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        return self._turns.get(turn_id)

    async def list_turns(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = [t for t in self._turns.values() if t["session_id"] == session_id]
        rows.sort(key=lambda t: t["created_at"], reverse=True)
        return rows[:limit]

    async def append_event(self, turn_id: str, event: dict[str, Any]) -> None:
        self._event_seq += 1
        self._events.setdefault(turn_id, []).append({**event, "id": self._event_seq})

    async def list_events(self, turn_id: str, limit: int = 500, after_id: int = 0) -> list[dict[str, Any]]:
        rows = [e for e in self._events.get(turn_id, []) if int(e.get("id", 0)) > after_id]
        return rows[-limit:]

    async def close(self) -> None:
        return None


# ================= PostgreSQL 实现 =================


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    persona: Mapped[dict] = mapped_column(JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(32), default="stranger")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    mood: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    category: Mapped[str] = mapped_column(String(32), default="fact")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TurnRow(Base):
    __tablename__ = "turns"

    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EventRow(Base):
    __tablename__ = "turn_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PostgresRepository(Repository):
    def __init__(self, database_url: str, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, echo=echo)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_db(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        async with self._session_factory() as session:
            existing = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
            if existing is not None:
                raise DuplicateUserError(username)
            row = UserRow(id=uuid.uuid4().hex, username=username, password_hash=password_hash)
            session.add(row)
            await session.commit()
            return UserRecord(id=row.id, username=row.username, password_hash=row.password_hash)

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
        return _user(row) if row else None

    async def get_user(self, user_id: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, user_id)
        return _user(row) if row else None

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with self._session_factory() as session:
            row = await session.get(SessionRow, session_id)
        return _session(row) if row else None

    async def save_session(self, record: SessionRecord) -> None:
        async with self._session_factory() as session:
            row = await session.get(SessionRow, record.session_id)
            now = datetime.now(timezone.utc)
            if row is None:
                row = SessionRow(session_id=record.session_id, created_at=now)
                session.add(row)
            row.user_id = record.user_id
            row.persona = record.persona.model_dump(mode="json")
            row.stage = record.stage.value
            row.score = record.score
            row.mood = record.mood.model_dump(mode="json")
            row.updated_at = now
            await session.commit()

    async def list_sessions_by_user(self, user_id: str) -> list[SessionRecord]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SessionRow)
                    .where(SessionRow.user_id == user_id)
                    .order_by(SessionRow.updated_at.desc())
                )
            ).scalars().all()
        return [_session(r) for r in rows]

    async def delete_session(self, session_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(MessageRow).where(MessageRow.session_id == session_id))
            await session.execute(delete(SessionRow).where(SessionRow.session_id == session_id))
            await session.commit()

    async def append_message(self, session_id: str, message: ChatMessage) -> None:
        async with self._session_factory() as session:
            session.add(
                MessageRow(
                    session_id=session_id,
                    role=message.role,
                    content=message.content,
                    tool_name=message.tool_name,
                )
            )
            await session.commit()

    async def recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MessageRow)
                    .where(MessageRow.session_id == session_id)
                    .order_by(MessageRow.id.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [
            ChatMessage(role=r.role, content=r.content, tool_name=r.tool_name, timestamp=r.created_at)
            for r in reversed(rows)
        ]

    async def clear_messages(self, session_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(MessageRow).where(MessageRow.session_id == session_id))
            await session.commit()

    async def save_memory(self, user_id: str, memory: MemoryItem) -> None:
        async with self._session_factory() as session:
            session.add(
                MemoryRow(
                    id=memory.id,
                    user_id=user_id,
                    content=memory.content,
                    importance=memory.importance,
                    category=memory.category,
                    tags=memory.tags,
                )
            )
            await session.commit()

    async def list_memories(self, user_id: str) -> list[MemoryItem]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MemoryRow)
                    .where(MemoryRow.user_id == user_id)
                    .order_by(MemoryRow.importance.desc())
                )
            ).scalars().all()
        return [
            MemoryItem(
                id=r.id, content=r.content, importance=r.importance, category=r.category,
                tags=list(r.tags or []), created_at=r.created_at,
            )
            for r in rows
        ]

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(MemoryRow).where(MemoryRow.id == memory_id, MemoryRow.user_id == user_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def clear_memories(self, user_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(delete(MemoryRow).where(MemoryRow.user_id == user_id))
            await session.commit()
            return int(result.rowcount or 0)

    async def save_turn(
        self, turn_id: str, session_id: str, user_id: str, status: str, payload: dict[str, Any]
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(TurnRow, turn_id)
            now = datetime.now(timezone.utc)
            if row is None:
                row = TurnRow(turn_id=turn_id, session_id=session_id, user_id=user_id, created_at=now)
                session.add(row)
            row.status = status
            row.payload = payload
            row.updated_at = now
            await session.commit()

    async def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(TurnRow, turn_id)
        if row is None:
            return None
        return {
            "turn_id": row.turn_id,
            "session_id": row.session_id,
            "user_id": row.user_id,
            "status": row.status,
            "payload": row.payload or {},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_turns(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TurnRow)
                    .where(TurnRow.session_id == session_id)
                    .order_by(TurnRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [
            {
                "turn_id": r.turn_id,
                "session_id": r.session_id,
                "user_id": r.user_id,
                "status": r.status,
                "payload": r.payload or {},
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]

    async def append_event(self, turn_id: str, event: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            session.add(EventRow(turn_id=turn_id, event=event))
            await session.commit()

    async def list_events(self, turn_id: str, limit: int = 500, after_id: int = 0) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(EventRow)
                    .where(EventRow.turn_id == turn_id, EventRow.id > after_id)
                    .order_by(EventRow.id.asc())
                    .limit(limit)
                )
            ).scalars().all()
        return [{"id": r.id, **(r.event or {})} for r in rows]

    async def close(self) -> None:
        await self._engine.dispose()


def _user(row: UserRow) -> UserRecord:
    return UserRecord(
        id=row.id, username=row.username, password_hash=row.password_hash, created_at=row.created_at
    )


def _session(row: SessionRow) -> SessionRecord:
    try:
        persona = Persona.model_validate(row.persona or {})
    except Exception:  # noqa: BLE001 - 脏数据回退默认人设
        persona = Persona()
    try:
        mood = MoodState.model_validate(row.mood or {})
    except Exception:  # noqa: BLE001
        mood = MoodState()
    try:
        stage = RelationshipStage(row.stage)
    except ValueError:
        stage = RelationshipStage.STRANGER
    return SessionRecord(
        session_id=row.session_id,
        user_id=row.user_id,
        persona=persona,
        stage=stage,
        score=float(row.score or 0.0),
        mood=mood,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_repository(database_url: str) -> Repository:
    if database_url:
        return PostgresRepository(database_url)
    logger.warning("[storage] DATABASE_URL 未配置，使用进程内内存仓库（重启丢失，仅演示/测试）")
    return InMemoryRepository()


__all__ = [
    "Repository",
    "InMemoryRepository",
    "PostgresRepository",
    "UserRecord",
    "SessionRecord",
    "DuplicateUserError",
    "create_repository",
]
