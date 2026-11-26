"""持久化仓库：PostgreSQL(async) + 内存降级。

表
--
| 表 | 用途 |
|---|---|
| users | 账号（BIGINT 主键、argon2id 密码、昵称/头像/创作者/状态） |
| user_auth | refresh token 会话（多设备、可撤销） |
| user_profile | 资料（性别/生日/bio/tags/traits/settings，仅本人可读写） |
| sessions | 会话（人设/关系/情绪），user_id 指向 users.id |
| messages | 对话历史 |
| memories | 长期记忆（目前 user 维度） |
| turns / turn_events | 单轮结果与全链路审计 |
"""
from __future__ import annotations

import ipaddress
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Identity,
    SmallInteger,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.companion import ChatMessage, MoodState, Persona, RelationshipStage
from app.domain.memory import MemoryItem

logger = logging.getLogger(__name__)

# 用户状态
STATUS_NORMAL = 1
STATUS_MUTED = 2
STATUS_BANNED = 3
STATUS_DELETED = 4


@dataclass
class UserRecord:
    id: int
    username: str
    password_hash: str
    nickname: str = ""
    avatar: str = ""
    is_creator: bool = False
    status: int = STATUS_NORMAL
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.id,
            "username": self.username,
            "nickname": self.nickname or self.username,
            "avatar": self.avatar,
            "is_creator": self.is_creator,
            "status": self.status,
        }


@dataclass
class SessionRecord:
    session_id: str
    user_id: int = 0
    persona: Persona = field(default_factory=Persona)
    stage: RelationshipStage = RelationshipStage.STRANGER
    score: float = 0.0
    mood: MoodState = field(default_factory=MoodState)
    character_id: int | None = None
    character_version_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


DEFAULT_PROFILE: dict[str, Any] = {
    "gender": 0,
    "birthday": None,
    "bio": "",
    "tags": [],
    "traits": {},
    "settings": {},
}


class DuplicateUserError(Exception):
    pass


class Repository(ABC):
    # ---- 用户 ----
    @abstractmethod
    async def create_user(
        self, username: str, password_hash: str, nickname: str = ""
    ) -> UserRecord: ...

    @abstractmethod
    async def get_user_by_username(self, username: str) -> UserRecord | None: ...

    @abstractmethod
    async def get_user(self, user_id: int) -> UserRecord | None: ...

    @abstractmethod
    async def update_user(self, user_id: int, **fields: Any) -> UserRecord | None: ...

    @abstractmethod
    async def touch_login(self, user_id: int) -> None: ...

    # ---- 资料 ----
    @abstractmethod
    async def get_profile(self, user_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def save_profile(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]: ...

    # ---- refresh token ----
    @abstractmethod
    async def create_auth(
        self,
        user_id: int,
        token_hash: str,
        *,
        device_id: str = "",
        platform: str = "",
        client_ip: str | None = None,
        user_agent: str = "",
        expires_at: datetime,
    ) -> int: ...

    @abstractmethod
    async def find_auth(self, token_hash: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def revoke_auth(self, auth_id: int, reason: str) -> None: ...

    @abstractmethod
    async def revoke_user_auths(self, user_id: int, reason: str) -> int: ...

    @abstractmethod
    async def touch_auth(self, auth_id: int) -> None: ...

    # ---- 会话 ----
    @abstractmethod
    async def get_session(self, session_id: str) -> SessionRecord | None: ...

    @abstractmethod
    async def save_session(self, record: SessionRecord) -> None: ...

    @abstractmethod
    async def list_sessions_by_user(self, user_id: int) -> list[SessionRecord]: ...

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
    async def save_memory(self, user_id: int, memory: MemoryItem) -> None: ...

    @abstractmethod
    async def list_memories(self, user_id: int) -> list[MemoryItem]: ...

    @abstractmethod
    async def delete_memory(self, user_id: int, memory_id: str) -> bool: ...

    @abstractmethod
    async def clear_memories(self, user_id: int) -> int: ...

    # ---- 轮次 ----
    @abstractmethod
    async def save_turn(
        self, turn_id: str, session_id: str, user_id: int, status: str, payload: dict[str, Any]
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
        self._users: dict[int, UserRecord] = {}
        self._usernames: dict[str, int] = {}
        self._profiles: dict[int, dict[str, Any]] = {}
        self._auths: dict[int, dict[str, Any]] = {}
        self._seq = 0
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[ChatMessage]] = {}
        self._memories: dict[int, dict[str, MemoryItem]] = {}
        self._turns: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._event_seq = 0

    async def create_user(self, username: str, password_hash: str, nickname: str = "") -> UserRecord:
        if username in self._usernames:
            raise DuplicateUserError(username)
        self._seq += 1
        user = UserRecord(
            id=self._seq,
            username=username,
            password_hash=password_hash,
            nickname=nickname or username,
            created_at=datetime.now(timezone.utc),
        )
        self._users[user.id] = user
        self._usernames[username] = user.id
        self._profiles[user.id] = dict(DEFAULT_PROFILE)
        return user

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        uid = self._usernames.get(username)
        return self._users.get(uid) if uid else None

    async def get_user(self, user_id: int) -> UserRecord | None:
        return self._users.get(int(user_id))

    async def update_user(self, user_id: int, **fields: Any) -> UserRecord | None:
        user = self._users.get(int(user_id))
        if user is None:
            return None
        for key in ("nickname", "avatar", "is_creator", "status", "password_hash"):
            if key in fields and fields[key] is not None:
                setattr(user, key, fields[key])
        user.updated_at = datetime.now(timezone.utc)
        return user

    async def touch_login(self, user_id: int) -> None:
        user = self._users.get(int(user_id))
        if user:
            user.last_login_at = datetime.now(timezone.utc)

    async def get_profile(self, user_id: int) -> dict[str, Any]:
        return dict(self._profiles.get(int(user_id), DEFAULT_PROFILE))

    async def save_profile(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        profile = dict(DEFAULT_PROFILE)
        profile.update(self._profiles.get(int(user_id), {}))
        profile.update({k: v for k, v in data.items() if v is not None})
        self._profiles[int(user_id)] = profile
        return profile

    async def create_auth(
        self,
        user_id: int,
        token_hash: str,
        *,
        device_id: str = "",
        platform: str = "",
        client_ip: str | None = None,
        user_agent: str = "",
        expires_at: datetime,
    ) -> int:
        self._seq += 1
        self._auths[self._seq] = {
            "id": self._seq,
            "user_id": int(user_id),
            "refresh_token_hash": token_hash,
            "device_id": device_id,
            "platform": platform,
            "client_ip": client_ip,
            "user_agent": user_agent[:255],
            "issued_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
            "last_active_at": None,
            "revoked_at": None,
            "revoked_reason": None,
        }
        return self._seq

    async def find_auth(self, token_hash: str) -> dict[str, Any] | None:
        for row in self._auths.values():
            if row["refresh_token_hash"] == token_hash:
                return dict(row)
        return None

    async def revoke_auth(self, auth_id: int, reason: str) -> None:
        row = self._auths.get(int(auth_id))
        if row and row["revoked_at"] is None:
            row["revoked_at"] = datetime.now(timezone.utc)
            row["revoked_reason"] = reason[:32]

    async def revoke_user_auths(self, user_id: int, reason: str) -> int:
        count = 0
        for row in self._auths.values():
            if row["user_id"] == int(user_id) and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
                row["revoked_reason"] = reason[:32]
                count += 1
        return count

    async def touch_auth(self, auth_id: int) -> None:
        row = self._auths.get(int(auth_id))
        if row:
            row["last_active_at"] = datetime.now(timezone.utc)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def save_session(self, record: SessionRecord) -> None:
        now = datetime.now(timezone.utc)
        existing = self._sessions.get(record.session_id)
        record.created_at = record.created_at or (existing.created_at if existing else now)
        record.updated_at = now
        self._sessions[record.session_id] = record

    async def list_sessions_by_user(self, user_id: int) -> list[SessionRecord]:
        rows = [s for s in self._sessions.values() if int(s.user_id) == int(user_id)]
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

    async def save_memory(self, user_id: int, memory: MemoryItem) -> None:
        self._memories.setdefault(int(user_id), {})[memory.id] = memory

    async def list_memories(self, user_id: int) -> list[MemoryItem]:
        rows = self._memories.get(int(user_id), {}).values()
        return sorted(rows, key=lambda m: m.importance, reverse=True)

    async def delete_memory(self, user_id: int, memory_id: str) -> bool:
        return self._memories.get(int(user_id), {}).pop(memory_id, None) is not None

    async def clear_memories(self, user_id: int) -> int:
        count = len(self._memories.get(int(user_id), {}))
        self._memories.pop(int(user_id), None)
        return count

    async def save_turn(
        self, turn_id: str, session_id: str, user_id: int, status: str, payload: dict[str, Any]
    ) -> None:
        now = datetime.now(timezone.utc)
        existing = self._turns.get(turn_id)
        self._turns[turn_id] = {
            "turn_id": turn_id,
            "session_id": session_id,
            "user_id": int(user_id),
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(64), default="")
    avatar: Mapped[str] = mapped_column(String(512), default="")
    is_creator: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=STATUS_NORMAL)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class UserAuthRow(Base):
    __tablename__ = "user_auth"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_id: Mapped[str] = mapped_column(String(128), default="")
    platform: Mapped[str] = mapped_column(String(16), default="")
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UserProfileRow(Base):
    __tablename__ = "user_profile"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gender: Mapped[int] = mapped_column(SmallInteger, default=0)
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    bio: Mapped[str] = mapped_column(String(255), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    traits: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    character_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    character_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
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
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
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


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_loopback_url(database_url: str) -> bool:
    """DSN 是否指向本机（本机 PG 不需要 TLS）。"""
    try:
        return (urlsplit(database_url).hostname or "") in _LOOPBACK_HOSTS
    except ValueError:
        return False


class PostgresRepository(Repository):
    def __init__(self, database_url: str, echo: bool = False) -> None:
        # asyncpg 0.32 会无条件探测 ~/.postgresql/postgresql.crt；home 不可读时抛
        # PermissionError（它只吞 FileNotFound）。本机库显式 ssl=False 可绕开；
        # 需要 TLS 时设 PG_SSL=true。
        connect_args: dict = {}
        if is_loopback_url(database_url) and os.getenv("PG_SSL", "").lower() not in ("1", "true", "yes"):
            connect_args["ssl"] = False
        self._engine: AsyncEngine = create_async_engine(
            database_url, echo=echo, connect_args=connect_args
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def init_db(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ---- 用户 ----
    async def create_user(self, username: str, password_hash: str, nickname: str = "") -> UserRecord:
        async with self._session_factory() as session:
            existing = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
            if existing is not None:
                raise DuplicateUserError(username)
            row = UserRow(
                username=username, password_hash=password_hash, nickname=nickname or username
            )
            session.add(row)
            await session.flush()
            session.add(UserProfileRow(user_id=row.id, tags=[], traits={}, settings={}))
            await session.commit()
            return _user(row)

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.username == username))
            ).scalar_one_or_none()
        return _user(row) if row else None

    async def get_user(self, user_id: int) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, int(user_id))
        return _user(row) if row else None

    async def update_user(self, user_id: int, **fields: Any) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, int(user_id))
            if row is None:
                return None
            for key in ("nickname", "avatar", "is_creator", "status", "password_hash"):
                if key in fields and fields[key] is not None:
                    setattr(row, key, fields[key])
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _user(row)

    async def touch_login(self, user_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, int(user_id))
            if row is not None:
                row.last_login_at = datetime.now(timezone.utc)
                await session.commit()

    # ---- 资料 ----
    async def get_profile(self, user_id: int) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(UserProfileRow, int(user_id))
        return _profile(row)

    async def save_profile(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(UserProfileRow, int(user_id))
            if row is None:
                row = UserProfileRow(user_id=int(user_id), tags=[], traits={}, settings={})
                session.add(row)
            for key in ("gender", "birthday", "bio"):
                if key in data and data[key] is not None:
                    setattr(row, key, data[key])
            for key in ("tags", "traits", "settings"):
                if key in data and data[key] is not None:
                    setattr(row, key, data[key])
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _profile(row)

    # ---- refresh token ----
    async def create_auth(
        self,
        user_id: int,
        token_hash: str,
        *,
        device_id: str = "",
        platform: str = "",
        client_ip: str | None = None,
        user_agent: str = "",
        expires_at: datetime,
    ) -> int:
        ip_value = None
        if client_ip:
            try:
                ip_value = ipaddress.ip_address(client_ip)
            except ValueError:
                ip_value = None
        async with self._session_factory() as session:
            row = UserAuthRow(
                user_id=int(user_id),
                refresh_token_hash=token_hash,
                device_id=device_id[:128],
                platform=platform[:16],
                client_ip=ip_value,
                user_agent=user_agent[:255],
                expires_at=expires_at,
            )
            session.add(row)
            await session.commit()
            return int(row.id)

    async def find_auth(self, token_hash: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(UserAuthRow).where(UserAuthRow.refresh_token_hash == token_hash)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "user_id": row.user_id,
            "device_id": row.device_id,
            "platform": row.platform,
            "expires_at": row.expires_at,
            "revoked_at": row.revoked_at,
            "revoked_reason": row.revoked_reason,
        }

    async def revoke_auth(self, auth_id: int, reason: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(UserAuthRow, int(auth_id))
            if row is not None and row.revoked_at is None:
                row.revoked_at = datetime.now(timezone.utc)
                row.revoked_reason = reason[:32]
                await session.commit()

    async def revoke_user_auths(self, user_id: int, reason: str) -> int:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(UserAuthRow).where(
                        UserAuthRow.user_id == int(user_id),
                        UserAuthRow.revoked_at.is_(None),
                    )
                )
            ).scalars().all()
            now = datetime.now(timezone.utc)
            for row in rows:
                row.revoked_at = now
                row.revoked_reason = reason[:32]
            await session.commit()
            return len(rows)

    async def touch_auth(self, auth_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(UserAuthRow, int(auth_id))
            if row is not None:
                row.last_active_at = datetime.now(timezone.utc)
                await session.commit()

    # ---- 会话 ----
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
            row.user_id = int(record.user_id)
            row.persona = record.persona.model_dump(mode="json")
            row.stage = record.stage.value
            row.score = record.score
            row.mood = record.mood.model_dump(mode="json")
            row.character_id = record.character_id
            row.character_version_id = record.character_version_id
            row.updated_at = now
            await session.commit()

    async def list_sessions_by_user(self, user_id: int) -> list[SessionRecord]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SessionRow)
                    .where(SessionRow.user_id == int(user_id))
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

    # ---- 记忆 ----
    async def save_memory(self, user_id: int, memory: MemoryItem) -> None:
        async with self._session_factory() as session:
            session.add(
                MemoryRow(
                    id=memory.id,
                    user_id=int(user_id),
                    content=memory.content,
                    importance=memory.importance,
                    category=memory.category,
                    tags=memory.tags,
                )
            )
            await session.commit()

    async def list_memories(self, user_id: int) -> list[MemoryItem]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MemoryRow)
                    .where(MemoryRow.user_id == int(user_id))
                    .order_by(MemoryRow.importance.desc())
                )
            ).scalars().all()
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

    async def delete_memory(self, user_id: int, memory_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(MemoryRow).where(MemoryRow.id == memory_id, MemoryRow.user_id == int(user_id))
            )
            await session.commit()
            return result.rowcount > 0

    async def clear_memories(self, user_id: int) -> int:
        async with self._session_factory() as session:
            result = await session.execute(delete(MemoryRow).where(MemoryRow.user_id == int(user_id)))
            await session.commit()
            return int(result.rowcount or 0)

    # ---- 轮次 ----
    async def save_turn(
        self, turn_id: str, session_id: str, user_id: int, status: str, payload: dict[str, Any]
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(TurnRow, turn_id)
            now = datetime.now(timezone.utc)
            if row is None:
                row = TurnRow(
                    turn_id=turn_id, session_id=session_id, user_id=int(user_id), created_at=now
                )
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
        id=int(row.id),
        username=row.username,
        password_hash=row.password_hash,
        nickname=row.nickname or row.username,
        avatar=row.avatar or "",
        is_creator=bool(row.is_creator),
        status=int(row.status or STATUS_NORMAL),
        last_login_at=row.last_login_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _profile(row: UserProfileRow | None) -> dict[str, Any]:
    if row is None:
        return dict(DEFAULT_PROFILE)
    return {
        "gender": int(row.gender or 0),
        "birthday": row.birthday.isoformat() if row.birthday else None,
        "bio": row.bio or "",
        "tags": list(row.tags or []),
        "traits": dict(row.traits or {}),
        "settings": dict(row.settings or {}),
    }


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
        user_id=int(row.user_id),
        persona=persona,
        stage=stage,
        score=float(row.score or 0.0),
        mood=mood,
        character_id=int(row.character_id) if row.character_id else None,
        character_version_id=int(row.character_version_id) if row.character_version_id else None,
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
    "STATUS_NORMAL",
    "STATUS_MUTED",
    "STATUS_BANNED",
    "STATUS_DELETED",
    "DEFAULT_PROFILE",
]
