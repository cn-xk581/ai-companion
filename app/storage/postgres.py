"""持久化仓库：PostgreSQL（SQLAlchemy 2.0 async）实现 + 内存降级实现。

商用部署用 PostgresRepository（DATABASE_URL 配置后自动启用）；
本地/测试无外部依赖时用 InMemoryRepository。
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, String, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.graph.state import ChatMessage, MemoryItem
from app.persona.models import MoodState, Persona, RelationshipStage

logger = logging.getLogger(__name__)


@dataclass
class SessionSnapshot:
    """会话持久化快照：人设 + 关系阶段 + 分数 + 情绪。"""

    session_id: str
    user_id: str = "default"
    persona: Persona = field(default_factory=Persona)
    relationship_stage: RelationshipStage = RelationshipStage.STRANGER
    relationship_score: float = 0.0
    mood: MoodState = field(default_factory=MoodState)


@dataclass
class UserRecord:
    id: str
    username: str
    password_hash: str
    created_at: datetime | None = None


class DuplicateUserError(Exception):
    pass


class Repository(ABC):
    @abstractmethod
    async def get_session(self, session_id: str) -> SessionSnapshot | None: ...

    @abstractmethod
    async def save_session(self, snap: SessionSnapshot) -> None: ...

    @abstractmethod
    async def append_chat(self, session_id: str, msg: ChatMessage) -> None: ...

    @abstractmethod
    async def get_recent_chat(self, session_id: str, limit: int) -> list[ChatMessage]: ...

    @abstractmethod
    async def save_memory(self, session_id: str, mem: MemoryItem) -> None: ...

    @abstractmethod
    async def list_memories(self, session_id: str) -> list[MemoryItem]: ...

    @abstractmethod
    async def delete_memory(self, session_id: str, memory_id: str) -> bool: ...

    @abstractmethod
    async def reset_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def list_session_ids(self) -> list[str]: ...

    @abstractmethod
    async def list_sessions_by_user(self, user_id: str) -> list[SessionSnapshot]: ...

    @abstractmethod
    async def close(self) -> None: ...

    # ---- 用户（JWT 体系） ----

    @abstractmethod
    async def create_user(self, username: str, password_hash: str) -> UserRecord: ...

    @abstractmethod
    async def get_user_by_username(self, username: str) -> UserRecord | None: ...

    @abstractmethod
    async def get_user(self, user_id: str) -> UserRecord | None: ...


# ================= 内存实现（降级） =================

class InMemoryRepository(Repository):
    def __init__(self) -> None:
        self._sessions: dict[str, SessionSnapshot] = {}
        self._chats: dict[str, list[ChatMessage]] = {}
        self._memories: dict[str, dict[str, MemoryItem]] = {}
        self._users: dict[str, UserRecord] = {}
        self._username_index: dict[str, str] = {}

    async def get_session(self, session_id: str) -> SessionSnapshot | None:
        return self._sessions.get(session_id)

    async def save_session(self, snap: SessionSnapshot) -> None:
        self._sessions[snap.session_id] = snap

    async def append_chat(self, session_id: str, msg: ChatMessage) -> None:
        self._chats.setdefault(session_id, []).append(msg)

    async def get_recent_chat(self, session_id: str, limit: int) -> list[ChatMessage]:
        return self._chats.get(session_id, [])[-limit:]

    async def save_memory(self, session_id: str, mem: MemoryItem) -> None:
        self._memories.setdefault(session_id, {})[mem.id] = mem

    async def list_memories(self, session_id: str) -> list[MemoryItem]:
        return sorted(self._memories.get(session_id, {}).values(), key=lambda m: m.importance, reverse=True)

    async def delete_memory(self, session_id: str, memory_id: str) -> bool:
        bucket = self._memories.get(session_id, {})
        if memory_id in bucket:
            del bucket[memory_id]
            return True
        return False

    async def reset_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._chats.pop(session_id, None)
        self._memories.pop(session_id, None)

    async def list_session_ids(self) -> list[str]:
        return list(self._sessions.keys())

    async def list_sessions_by_user(self, user_id: str) -> list[SessionSnapshot]:
        return [s for s in self._sessions.values() if s.user_id == user_id]

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        if username in self._username_index:
            raise DuplicateUserError(username)
        user = UserRecord(id=uuid.uuid4().hex, username=username, password_hash=password_hash)
        self._users[user.id] = user
        self._username_index[username] = user.id
        return user

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        uid = self._username_index.get(username)
        return self._users.get(uid) if uid else None

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self._users.get(user_id)

    async def close(self) -> None:
        return None


# ================= PostgreSQL 实现 =================

class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    persona: Mapped[str] = mapped_column(Text, default="{}")
    relationship_stage: Mapped[str] = mapped_column(String(32), default="stranger")
    relationship_score: Mapped[float] = mapped_column(Float, default=0.0)
    mood: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatRow(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    category: Mapped[str] = mapped_column(String(32), default="fact")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PostgresRepository(Repository):
    def __init__(self, database_url: str, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, echo=echo)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_db(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_session(self, session_id: str) -> SessionSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(SessionRow, session_id)
        if row is None:
            return None
        return _row_to_snapshot(row)

    async def save_session(self, snap: SessionSnapshot) -> None:
        async with self._session_factory() as session:
            row = await session.get(SessionRow, snap.session_id)
            if row is None:
                row = SessionRow(session_id=snap.session_id, user_id=snap.user_id)
                session.add(row)
            row.user_id = snap.user_id
            row.persona = snap.persona.model_dump_json()
            row.relationship_stage = snap.relationship_stage.value
            row.relationship_score = snap.relationship_score
            row.mood = snap.mood.model_dump_json()
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()

    async def append_chat(self, session_id: str, msg: ChatMessage) -> None:
        async with self._session_factory() as session:
            session.add(
                ChatRow(
                    session_id=session_id,
                    role=msg.role,
                    content=msg.content,
                    tool_name=msg.tool_name,
                )
            )
            await session.commit()

    async def get_recent_chat(self, session_id: str, limit: int) -> list[ChatMessage]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ChatRow)
                        .where(ChatRow.session_id == session_id)
                        .order_by(ChatRow.id.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [
            ChatMessage(role=r.role, content=r.content, tool_name=r.tool_name, timestamp=r.created_at)
            for r in reversed(rows)
        ]

    async def save_memory(self, session_id: str, mem: MemoryItem) -> None:
        async with self._session_factory() as session:
            await session.merge(
                MemoryRow(
                    id=mem.id,
                    session_id=session_id,
                    content=mem.content,
                    importance=mem.importance,
                    category=mem.category,
                    tags=mem.tags,
                )
            )
            await session.commit()

    async def list_memories(self, session_id: str) -> list[MemoryItem]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(MemoryRow)
                        .where(MemoryRow.session_id == session_id)
                        .order_by(MemoryRow.importance.desc())
                    )
                )
                .scalars()
                .all()
            )
        return [
            MemoryItem(
                id=r.id,
                content=r.content,
                importance=r.importance,
                category=r.category,
                tags=list(r.tags or []),
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def delete_memory(self, session_id: str, memory_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(MemoryRow).where(MemoryRow.id == memory_id, MemoryRow.session_id == session_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def reset_session(self, session_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(ChatRow).where(ChatRow.session_id == session_id))
            await session.execute(delete(MemoryRow).where(MemoryRow.session_id == session_id))
            await session.execute(delete(SessionRow).where(SessionRow.session_id == session_id))
            await session.commit()

    async def list_session_ids(self) -> list[str]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(SessionRow.session_id))).scalars().all()
        return list(rows)

    async def list_sessions_by_user(self, user_id: str) -> list[SessionSnapshot]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(select(SessionRow).where(SessionRow.user_id == user_id))
            ).scalars().all()
        return [_row_to_snapshot(r) for r in rows]

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        async with self._session_factory() as session:
            existing = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
            if existing is not None:
                raise DuplicateUserError(username)
            user = UserRow(id=uuid.uuid4().hex, username=username, password_hash=password_hash)
            session.add(user)
            await session.commit()
            return UserRecord(id=user.id, username=user.username, password_hash=user.password_hash, created_at=user.created_at)

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
        return _row_to_user(row) if row else None

    async def get_user(self, user_id: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, user_id)
        return _row_to_user(row) if row else None

    async def close(self) -> None:
        await self._engine.dispose()


def _row_to_user(row: UserRow) -> UserRecord:
    return UserRecord(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        created_at=row.created_at,
    )


def _row_to_snapshot(row: SessionRow) -> SessionSnapshot:
    try:
        persona = Persona.model_validate_json(row.persona) if row.persona else Persona()
    except Exception:  # noqa: BLE001
        persona = Persona()
    try:
        mood = MoodState.model_validate_json(row.mood) if row.mood else MoodState()
    except Exception:  # noqa: BLE001
        mood = MoodState()
    try:
        stage = RelationshipStage(row.relationship_stage)
    except ValueError:
        stage = RelationshipStage.STRANGER
    return SessionSnapshot(
        session_id=row.session_id,
        user_id=row.user_id,
        persona=persona,
        relationship_stage=stage,
        relationship_score=row.relationship_score,
        mood=mood,
    )


def create_repository(database_url: str) -> Repository:
    if database_url:
        return PostgresRepository(database_url)
    logger.warning("DATABASE_URL 未配置，使用进程内内存仓库（重启数据丢失，仅演示/测试）")
    return InMemoryRepository()
