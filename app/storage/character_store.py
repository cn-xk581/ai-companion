"""角色（聊天对象）存储：PostgreSQL + 内存实现。

表：characters / character_versions / character_lorebooks /
    character_lorebook_entries / character_media
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    cast,
    delete,
    func,
    or_,
    select,
)
from sqlalchemy import Text as _SAText
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.postgres import Base, PostgresRepository

logger = logging.getLogger(__name__)

STATUS_DRAFT, STATUS_REVIEW, STATUS_ONLINE, STATUS_OFFLINE, STATUS_BANNED = 1, 2, 3, 4, 5
VIS_PUBLIC, VIS_PRIVATE, VIS_LINK = 1, 2, 3


@dataclass
class CharacterRecord:
    id: int
    creator_id: int
    name: str
    nickname: str = ""
    summary: str = ""
    cover_url: str = ""
    tags: list[str] = field(default_factory=list)
    visibility: int = VIS_PRIVATE
    status: int = STATUS_ONLINE
    current_version_id: int | None = None
    source_character_id: int | None = None
    card_spec: str = ""
    card_spec_version: str = ""
    import_source: str = ""
    chat_cnt: int = 0
    user_cnt: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def public(self) -> dict[str, Any]:
        return {
            "character_id": self.id,
            "creator_id": self.creator_id,
            "name": self.name,
            "nickname": self.nickname,
            "summary": self.summary,
            "cover_url": self.cover_url,
            "tags": self.tags,
            "visibility": self.visibility,
            "status": self.status,
            "card_spec": self.card_spec,
            "card_spec_version": self.card_spec_version,
            "import_source": self.import_source,
            "chat_cnt": self.chat_cnt,
            "user_cnt": self.user_cnt,
            "current_version_id": self.current_version_id,
        }


@dataclass
class VersionRecord:
    id: int
    character_id: int
    version_no: int
    persona: dict[str, Any]
    greeting: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    dialogue_samples: list[dict[str, str]] = field(default_factory=list)
    raw_card: dict[str, Any] = field(default_factory=dict)
    card_file_key: str = ""
    card_file_url: str = ""
    change_note: str = ""
    audit_status: int = 2
    created_by: int = 0
    created_at: datetime | None = None


class CharacterStore(ABC):
    @abstractmethod
    async def create_character(self, **fields: Any) -> CharacterRecord: ...

    @abstractmethod
    async def get(self, character_id: int) -> CharacterRecord | None: ...

    @abstractmethod
    async def list_by_creator(self, creator_id: int, limit: int = 100) -> list[CharacterRecord]: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[CharacterRecord]: ...

    @abstractmethod
    async def update(self, character_id: int, **fields: Any) -> CharacterRecord | None: ...

    @abstractmethod
    async def set_current_version(self, character_id: int, version_id: int) -> None: ...

    @abstractmethod
    async def add_version(self, **fields: Any) -> VersionRecord: ...

    @abstractmethod
    async def get_version(self, version_id: int) -> VersionRecord | None: ...

    @abstractmethod
    async def get_current_version(self, character_id: int) -> VersionRecord | None: ...

    @abstractmethod
    async def list_versions(self, character_id: int, limit: int = 50) -> list[VersionRecord]: ...

    @abstractmethod
    async def save_lorebook(self, character_id: int, book: dict[str, Any] | None) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_lorebook(self, character_id: int) -> dict[str, Any] | None: ...

    @abstractmethod
    async def add_media(
        self, character_id: int, media_type: int, url: str, asset_name: str = "", ext: str = "", sort_no: int = 0
    ) -> None: ...

    @abstractmethod
    async def list_media(self, character_id: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def bump_chat(self, character_id: int, new_user: bool = False) -> None: ...

    @abstractmethod
    async def soft_delete(self, character_id: int) -> None: ...


# ---------------- 内存实现 ----------------


class InMemoryCharacterStore(CharacterStore):
    def __init__(self) -> None:
        self._chars: dict[int, CharacterRecord] = {}
        self._versions: dict[int, VersionRecord] = {}
        self._lorebooks: dict[int, dict[str, Any]] = {}
        self._media: dict[int, list[dict[str, Any]]] = {}
        self._seq = 0
        self._vseq = 0

    async def create_character(self, **fields: Any) -> CharacterRecord:
        self._seq += 1
        now = datetime.now(timezone.utc)
        record = CharacterRecord(
            id=self._seq,
            creator_id=int(fields["creator_id"]),
            name=fields["name"],
            nickname=fields.get("nickname") or "",
            summary=fields.get("summary") or "",
            cover_url=fields.get("cover_url") or "",
            tags=list(fields.get("tags") or []),
            visibility=int(fields.get("visibility", VIS_PRIVATE)),
            status=int(fields.get("status", STATUS_ONLINE)),
            source_character_id=fields.get("source_character_id"),
            card_spec=fields.get("card_spec") or "",
            card_spec_version=fields.get("card_spec_version") or "",
            import_source=fields.get("import_source") or "",
            created_at=now,
            updated_at=now,
        )
        self._chars[record.id] = record
        return record

    async def get(self, character_id: int) -> CharacterRecord | None:
        return self._chars.get(int(character_id))

    async def list_by_creator(self, creator_id: int, limit: int = 100) -> list[CharacterRecord]:
        rows = [c for c in self._chars.values() if c.creator_id == int(creator_id)]
        rows.sort(key=lambda c: c.created_at or datetime.now(timezone.utc), reverse=True)
        return rows[:limit]

    async def search(self, query: str, limit: int = 20) -> list[CharacterRecord]:
        q = (query or "").strip().lower()
        if not q:
            return []
        rows = [
            c
            for c in self._chars.values()
            if c.status == STATUS_ONLINE
            and (
                q in (c.name or "").lower()
                or q in (c.nickname or "").lower()
                or any(q in str(t).lower() for t in (c.tags or []))
            )
        ]
        rows.sort(key=lambda c: (c.chat_cnt, c.created_at or datetime.now(timezone.utc)), reverse=True)
        return rows[:limit]

    async def update(self, character_id: int, **fields: Any) -> CharacterRecord | None:
        char = self._chars.get(int(character_id))
        if char is None:
            return None
        for key in (
            "name",
            "nickname",
            "summary",
            "cover_url",
            "tags",
            "visibility",
            "status",
            "current_version_id",
        ):
            if key in fields and fields[key] is not None:
                setattr(char, key, fields[key])
        char.updated_at = datetime.now(timezone.utc)
        return char

    async def set_current_version(self, character_id: int, version_id: int) -> None:
        await self.update(character_id, current_version_id=int(version_id))

    async def add_version(self, **fields: Any) -> VersionRecord:
        character_id = int(fields["character_id"])
        existing = [v for v in self._versions.values() if v.character_id == character_id]
        version_no = int(fields.get("version_no") or max((v.version_no for v in existing), default=0) + 1)
        self._vseq += 1
        record = VersionRecord(
            id=self._vseq,
            character_id=character_id,
            version_no=version_no,
            persona=dict(fields.get("persona") or {}),
            greeting=fields.get("greeting") or "",
            alternate_greetings=list(fields.get("alternate_greetings") or []),
            dialogue_samples=list(fields.get("dialogue_samples") or []),
            raw_card=dict(fields.get("raw_card") or {}),
            card_file_key=fields.get("card_file_key") or "",
            card_file_url=fields.get("card_file_url") or "",
            change_note=fields.get("change_note") or "",
            audit_status=int(fields.get("audit_status", 2)),
            created_by=int(fields.get("created_by") or 0),
            created_at=datetime.now(timezone.utc),
        )
        self._versions[record.id] = record
        return record

    async def get_version(self, version_id: int) -> VersionRecord | None:
        return self._versions.get(int(version_id))

    async def get_current_version(self, character_id: int) -> VersionRecord | None:
        char = self._chars.get(int(character_id))
        if char is None or not char.current_version_id:
            return None
        return self._versions.get(int(char.current_version_id))

    async def list_versions(self, character_id: int, limit: int = 50) -> list[VersionRecord]:
        rows = [v for v in self._versions.values() if v.character_id == int(character_id)]
        rows.sort(key=lambda v: v.version_no, reverse=True)
        return rows[:limit]

    async def save_lorebook(self, character_id: int, book: dict[str, Any] | None) -> dict[str, Any] | None:
        if not book:
            self._lorebooks.pop(int(character_id), None)
            return None
        self._lorebooks[int(character_id)] = dict(book)
        return book

    async def get_lorebook(self, character_id: int) -> dict[str, Any] | None:
        return self._lorebooks.get(int(character_id))

    async def add_media(
        self, character_id: int, media_type: int, url: str, asset_name: str = "", ext: str = "", sort_no: int = 0
    ) -> None:
        self._media.setdefault(int(character_id), []).append(
            {"media_type": media_type, "url": url, "asset_name": asset_name, "ext": ext, "sort_no": sort_no}
        )

    async def list_media(self, character_id: int) -> list[dict[str, Any]]:
        return list(self._media.get(int(character_id), []))

    async def bump_chat(self, character_id: int, new_user: bool = False) -> None:
        char = self._chars.get(int(character_id))
        if char:
            char.chat_cnt += 1
            if new_user:
                char.user_cnt += 1

    async def soft_delete(self, character_id: int) -> None:
        char = self._chars.get(int(character_id))
        if char:
            char.status = STATUS_BANNED
            char.visibility = VIS_PRIVATE
            char.updated_at = datetime.now(timezone.utc)


# ---------------- PostgreSQL ----------------


class CharacterRow(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(64))
    nickname: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(String(512), default="")
    cover_url: Mapped[str] = mapped_column(String(512), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[int] = mapped_column(SmallInteger, default=VIS_PRIVATE)
    status: Mapped[int] = mapped_column(SmallInteger, default=STATUS_ONLINE)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_character_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    card_spec: Mapped[str] = mapped_column(String(32), default="")
    card_spec_version: Mapped[str] = mapped_column(String(16), default="")
    import_source: Mapped[str] = mapped_column(String(16), default="")
    chat_cnt: Mapped[int] = mapped_column(BigInteger, default=0)
    user_cnt: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CharacterVersionRow(Base):
    __tablename__ = "character_versions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    character_id: Mapped[int] = mapped_column(BigInteger, index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    persona: Mapped[dict] = mapped_column(JSON)
    greeting: Mapped[str] = mapped_column(Text, default="")
    alternate_greetings: Mapped[list] = mapped_column(JSON, default=list)
    dialogue_samples: Mapped[list] = mapped_column(JSON, default=list)
    raw_card: Mapped[dict] = mapped_column(JSON, default=dict)
    card_file_key: Mapped[str] = mapped_column(String(512), default="")
    card_file_url: Mapped[str] = mapped_column(String(512), default="")
    change_note: Mapped[str] = mapped_column(String(255), default="")
    audit_status: Mapped[int] = mapped_column(SmallInteger, default=2)
    audit_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CharacterLorebookRow(Base):
    __tablename__ = "character_lorebooks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    character_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    scan_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recursive_scanning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    extensions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CharacterLorebookEntryRow(Base):
    __tablename__ = "character_lorebook_entries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    lorebook_id: Mapped[int] = mapped_column(BigInteger, index=True)
    uid: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(128), default="")
    keys: Mapped[list] = mapped_column(JSON, default=list)
    secondary_keys: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    constant: Mapped[bool] = mapped_column(Boolean, default=False)
    selective: Mapped[bool] = mapped_column(Boolean, default=False)
    insertion_order: Mapped[int] = mapped_column(Integer, default=100)
    position: Mapped[str] = mapped_column(String(24), default="after_char")
    case_sensitive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_regex: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CharacterMediaRow(Base):
    __tablename__ = "character_media"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    character_id: Mapped[int] = mapped_column(BigInteger, index=True)
    media_type: Mapped[int] = mapped_column(SmallInteger, default=1)
    asset_name: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(String(512))
    ext: Mapped[str] = mapped_column(String(16), default="")
    sort_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PostgresCharacterStore(CharacterStore):
    def __init__(self, engine: AsyncEngine) -> None:
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_character(self, **fields: Any) -> CharacterRecord:
        now = datetime.now(timezone.utc)
        async with self._factory() as session:
            row = CharacterRow(
                creator_id=int(fields["creator_id"]),
                name=fields["name"][:64],
                nickname=(fields.get("nickname") or "")[:64],
                summary=(fields.get("summary") or "")[:512],
                cover_url=(fields.get("cover_url") or "")[:512],
                tags=list(fields.get("tags") or []),
                visibility=int(fields.get("visibility", VIS_PRIVATE)),
                status=int(fields.get("status", STATUS_ONLINE)),
                source_character_id=fields.get("source_character_id"),
                card_spec=(fields.get("card_spec") or "")[:32],
                card_spec_version=(fields.get("card_spec_version") or "")[:16],
                import_source=(fields.get("import_source") or "")[:16],
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            return _char(row)

    async def get(self, character_id: int) -> CharacterRecord | None:
        async with self._factory() as session:
            row = await session.get(CharacterRow, int(character_id))
        if row is None or row.deleted_at is not None:
            return None
        return _char(row)

    async def list_by_creator(self, creator_id: int, limit: int = 100) -> list[CharacterRecord]:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(CharacterRow)
                    .where(
                        CharacterRow.creator_id == int(creator_id),
                        CharacterRow.deleted_at.is_(None),
                    )
                    .order_by(CharacterRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [_char(r) for r in rows]

    async def search(self, query: str, limit: int = 20) -> list[CharacterRecord]:
        q = (query or "").strip()
        if not q:
            return []
        pattern = f"%{q}%"
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(CharacterRow)
                    .where(
                        CharacterRow.deleted_at.is_(None),
                        CharacterRow.status == STATUS_ONLINE,
                        or_(
                            CharacterRow.name.ilike(pattern),
                            CharacterRow.nickname.ilike(pattern),
                            cast(CharacterRow.tags, Text).ilike(pattern),
                        ),
                    )
                    .order_by(CharacterRow.chat_cnt.desc(), CharacterRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [_char(r) for r in rows]

    async def update(self, character_id: int, **fields: Any) -> CharacterRecord | None:
        async with self._factory() as session:
            row = await session.get(CharacterRow, int(character_id))
            if row is None:
                return None
            for key in (
                "name",
                "nickname",
                "summary",
                "cover_url",
                "tags",
                "visibility",
                "status",
                "current_version_id",
            ):
                if key in fields and fields[key] is not None:
                    setattr(row, key, fields[key])
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _char(row)

    async def set_current_version(self, character_id: int, version_id: int) -> None:
        await self.update(character_id, current_version_id=int(version_id))

    async def add_version(self, **fields: Any) -> VersionRecord:
        character_id = int(fields["character_id"])
        async with self._factory() as session:
            current = (
                await session.execute(
                    select(func.max(CharacterVersionRow.version_no)).where(
                        CharacterVersionRow.character_id == character_id
                    )
                )
            ).scalar()
            row = CharacterVersionRow(
                character_id=character_id,
                version_no=int(fields.get("version_no") or (current or 0) + 1),
                persona=dict(fields.get("persona") or {}),
                greeting=fields.get("greeting") or "",
                alternate_greetings=list(fields.get("alternate_greetings") or []),
                dialogue_samples=list(fields.get("dialogue_samples") or []),
                raw_card=dict(fields.get("raw_card") or {}),
                card_file_key=(fields.get("card_file_key") or "")[:512],
                card_file_url=(fields.get("card_file_url") or "")[:512],
                change_note=(fields.get("change_note") or "")[:255],
                audit_status=int(fields.get("audit_status", 2)),
                created_by=int(fields.get("created_by") or 0),
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            await session.commit()
            return _version(row)

    async def get_version(self, version_id: int) -> VersionRecord | None:
        async with self._factory() as session:
            row = await session.get(CharacterVersionRow, int(version_id))
        return _version(row) if row else None

    async def get_current_version(self, character_id: int) -> VersionRecord | None:
        async with self._factory() as session:
            char = await session.get(CharacterRow, int(character_id))
            if char is None or not char.current_version_id:
                return None
            row = await session.get(CharacterVersionRow, int(char.current_version_id))
        return _version(row) if row else None

    async def list_versions(self, character_id: int, limit: int = 50) -> list[VersionRecord]:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(CharacterVersionRow)
                    .where(CharacterVersionRow.character_id == int(character_id))
                    .order_by(CharacterVersionRow.version_no.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [_version(r) for r in rows]

    async def save_lorebook(self, character_id: int, book: dict[str, Any] | None) -> dict[str, Any] | None:
        async with self._factory() as session:
            existing = (
                await session.execute(
                    select(CharacterLorebookRow).where(
                        CharacterLorebookRow.character_id == int(character_id)
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                await session.execute(
                    delete(CharacterLorebookEntryRow).where(
                        CharacterLorebookEntryRow.lorebook_id == existing.id
                    )
                )
                await session.execute(
                    delete(CharacterLorebookRow).where(CharacterLorebookRow.id == existing.id)
                )
            if not book:
                await session.commit()
                return None
            now = datetime.now(timezone.utc)
            lb = CharacterLorebookRow(
                character_id=int(character_id),
                name=(book.get("name") or "")[:128],
                description=book.get("description") or "",
                scan_depth=book.get("scan_depth"),
                token_budget=book.get("token_budget"),
                recursive_scanning=book.get("recursive_scanning"),
                extensions=book.get("extensions") or {},
                created_at=now,
                updated_at=now,
            )
            session.add(lb)
            await session.flush()
            for entry in book.get("entries") or []:
                session.add(
                    CharacterLorebookEntryRow(
                        lorebook_id=lb.id,
                        uid=str(entry.get("uid") or "")[:64],
                        name=(entry.get("name") or "")[:128],
                        keys=list(entry.get("keys") or []),
                        secondary_keys=list(entry.get("secondary_keys") or []),
                        content=entry.get("content") or "",
                        enabled=bool(entry.get("enabled", True)),
                        constant=bool(entry.get("constant", False)),
                        selective=bool(entry.get("selective", False)),
                        insertion_order=int(entry.get("insertion_order") or 100),
                        position=(entry.get("position") or "after_char")[:24],
                        case_sensitive=entry.get("case_sensitive"),
                        use_regex=bool(entry.get("use_regex", False)),
                        priority=entry.get("priority"),
                        comment=entry.get("comment") or "",
                        raw=dict(entry),
                    )
                )
            await session.commit()
            return book

    async def get_lorebook(self, character_id: int) -> dict[str, Any] | None:
        async with self._factory() as session:
            lb = (
                await session.execute(
                    select(CharacterLorebookRow).where(
                        CharacterLorebookRow.character_id == int(character_id)
                    )
                )
            ).scalar_one_or_none()
            if lb is None:
                return None
            entries = (
                await session.execute(
                    select(CharacterLorebookEntryRow)
                    .where(CharacterLorebookEntryRow.lorebook_id == lb.id)
                    .order_by(CharacterLorebookEntryRow.insertion_order.asc())
                )
            ).scalars().all()
        return {
            "name": lb.name,
            "description": lb.description,
            "scan_depth": lb.scan_depth,
            "token_budget": lb.token_budget,
            "recursive_scanning": lb.recursive_scanning,
            "extensions": lb.extensions or {},
            "entries": [
                {
                    "uid": e.uid,
                    "name": e.name,
                    "keys": e.keys or [],
                    "secondary_keys": e.secondary_keys or [],
                    "content": e.content,
                    "enabled": e.enabled,
                    "constant": e.constant,
                    "selective": e.selective,
                    "insertion_order": e.insertion_order,
                    "position": e.position,
                    "case_sensitive": e.case_sensitive,
                    "use_regex": e.use_regex,
                    "priority": e.priority,
                    "comment": e.comment,
                    "extensions": (e.raw or {}).get("extensions") or {},
                }
                for e in entries
            ],
        }

    async def add_media(
        self, character_id: int, media_type: int, url: str, asset_name: str = "", ext: str = "", sort_no: int = 0
    ) -> None:
        async with self._factory() as session:
            session.add(
                CharacterMediaRow(
                    character_id=int(character_id),
                    media_type=int(media_type),
                    url=url[:512],
                    asset_name=asset_name[:64],
                    ext=ext[:16],
                    sort_no=int(sort_no),
                )
            )
            await session.commit()

    async def list_media(self, character_id: int) -> list[dict[str, Any]]:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(CharacterMediaRow)
                    .where(CharacterMediaRow.character_id == int(character_id))
                    .order_by(CharacterMediaRow.media_type.asc(), CharacterMediaRow.sort_no.asc())
                )
            ).scalars().all()
        return [
            {
                "media_type": r.media_type,
                "url": r.url,
                "asset_name": r.asset_name,
                "ext": r.ext,
                "sort_no": r.sort_no,
            }
            for r in rows
        ]

    async def bump_chat(self, character_id: int, new_user: bool = False) -> None:
        async with self._factory() as session:
            row = await session.get(CharacterRow, int(character_id))
            if row is None:
                return
            row.chat_cnt = int(row.chat_cnt or 0) + 1
            if new_user:
                row.user_cnt = int(row.user_cnt or 0) + 1
            await session.commit()

    async def soft_delete(self, character_id: int) -> None:
        async with self._factory() as session:
            row = await session.get(CharacterRow, int(character_id))
            if row is not None:
                row.deleted_at = datetime.now(timezone.utc)
                row.status = STATUS_BANNED
                row.visibility = VIS_PRIVATE
                await session.commit()


def _char(row: CharacterRow) -> CharacterRecord:
    return CharacterRecord(
        id=int(row.id),
        creator_id=int(row.creator_id),
        name=row.name,
        nickname=row.nickname or "",
        summary=row.summary or "",
        cover_url=row.cover_url or "",
        tags=list(row.tags or []),
        visibility=int(row.visibility or VIS_PRIVATE),
        status=int(row.status or STATUS_ONLINE),
        current_version_id=int(row.current_version_id) if row.current_version_id else None,
        source_character_id=int(row.source_character_id) if row.source_character_id else None,
        card_spec=row.card_spec or "",
        card_spec_version=row.card_spec_version or "",
        import_source=row.import_source or "",
        chat_cnt=int(row.chat_cnt or 0),
        user_cnt=int(row.user_cnt or 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version(row: CharacterVersionRow) -> VersionRecord:
    return VersionRecord(
        id=int(row.id),
        character_id=int(row.character_id),
        version_no=int(row.version_no),
        persona=dict(row.persona or {}),
        greeting=row.greeting or "",
        alternate_greetings=list(row.alternate_greetings or []),
        dialogue_samples=list(row.dialogue_samples or []),
        raw_card=dict(row.raw_card or {}),
        card_file_key=row.card_file_key or "",
        card_file_url=row.card_file_url or "",
        change_note=row.change_note or "",
        audit_status=int(row.audit_status or 2),
        created_by=int(row.created_by or 0),
        created_at=row.created_at,
    )


def create_character_store(repo: Any) -> CharacterStore:
    if isinstance(repo, PostgresRepository):
        return PostgresCharacterStore(repo.engine)
    return InMemoryCharacterStore()


__all__ = [
    "CharacterStore",
    "InMemoryCharacterStore",
    "PostgresCharacterStore",
    "CharacterRecord",
    "VersionRecord",
    "create_character_store",
    "STATUS_ONLINE",
    "STATUS_DRAFT",
    "VIS_PRIVATE",
    "VIS_PUBLIC",
]
