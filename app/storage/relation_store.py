"""羁绊记忆存储：PostgreSQL + 内存实现。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.relations import (
    EP_ARCHIVED,
    EP_ACTIVE,
    FACT_ACTIVE,
    FACT_ARCHIVED,
    FACT_PENDING,
    FACT_SUPERSEDED,
    Episode,
    Fact,
    Relation,
    SessionSummary,
    content_hash,
    fact_key_norm,
    now,
)
from app.storage.postgres import Base, PostgresRepository

logger = logging.getLogger(__name__)


class RelationRow(Base):
    __tablename__ = "user_character_relations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    character_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_persona_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_nickname: Mapped[str | None] = mapped_column(String(32), nullable=True)
    affinity_score: Mapped[int] = mapped_column(Integer, default=0)
    relationship_stage: Mapped[str] = mapped_column(String(32), default="stranger")
    current_mood: Mapped[str] = mapped_column(String(32), default="neutral")
    total_turns: Mapped[int] = mapped_column(Integer, default=0)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_consolidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FactRow(Base):
    __tablename__ = "relation_facts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    relation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    category: Mapped[str] = mapped_column(String(16))
    fact_key: Mapped[str] = mapped_column(String(64))
    fact_key_norm: Mapped[str] = mapped_column(String(64), index=True)
    fact_value: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=FACT_ACTIVE)
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(32), default="")
    source_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EpisodeRow(Base):
    __tablename__ = "relation_episodes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    relation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="event")
    summary: Mapped[str] = mapped_column(Text)
    emotion_tag: Mapped[str | None] = mapped_column(String(16), nullable=True)
    importance: Mapped[int] = mapped_column(SmallInteger, default=3)
    status: Mapped[int] = mapped_column(SmallInteger, default=EP_ACTIVE)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    source_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(32), default="")
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SummaryRow(Base):
    __tablename__ = "session_summaries"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    covered_until_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    covered_turns: Mapped[int] = mapped_column(Integer, default=0)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RelationStore(ABC):
    @abstractmethod
    async def ensure_relation(self, user_id: int, character_id: int) -> Relation: ...

    @abstractmethod
    async def get_relation(self, relation_id: int) -> Relation | None: ...

    @abstractmethod
    async def get_by_user_character(self, user_id: int, character_id: int) -> Relation | None: ...

    @abstractmethod
    async def update_relation(self, relation_id: int, **fields: Any) -> Relation | None: ...

    @abstractmethod
    async def touch_relation(self, relation_id: int) -> None: ...

    @abstractmethod
    async def add_fact(self, **fields: Any) -> Fact: ...

    @abstractmethod
    async def get_fact(self, fact_id: int) -> Fact | None: ...

    @abstractmethod
    async def find_active_fact(self, relation_id: int, key_norm: str) -> Fact | None: ...

    @abstractmethod
    async def list_facts(self, relation_id: int, include_history: bool = False) -> list[Fact]: ...

    @abstractmethod
    async def update_fact(self, fact_id: int, **fields: Any) -> None: ...

    @abstractmethod
    async def add_episode(self, **fields: Any) -> Episode: ...

    @abstractmethod
    async def get_episode(self, episode_id: int) -> Episode | None: ...

    @abstractmethod
    async def list_episodes(
        self, relation_id: int, include_archived: bool = False, limit: int = 200
    ) -> list[Episode]: ...

    @abstractmethod
    async def update_episode(self, episode_id: int, **fields: Any) -> None: ...

    @abstractmethod
    async def archive_episodes(self, stale_before: datetime, max_importance: int = 3) -> int: ...

    @abstractmethod
    async def get_summary(self, session_id: str) -> SessionSummary | None: ...

    @abstractmethod
    async def save_summary(self, summary: SessionSummary) -> None: ...

    @abstractmethod
    async def clear_session_memories(self, session_id: str) -> int: ...

    @abstractmethod
    async def decay_affinity(self, per_day: int = 1) -> int: ...

    @abstractmethod
    async def list_relations(self, user_id: int | None = None) -> list[Relation]: ...

    @abstractmethod
    async def archive_pending_facts(self, stale_before: datetime) -> int: ...

    @abstractmethod
    async def clear_relation_memories(self, relation_id: int) -> int: ...


# ---------------- 内存实现 ----------------


class InMemoryRelationStore(RelationStore):
    def __init__(self) -> None:
        self._relations: dict[int, Relation] = {}
        self._facts: dict[int, Fact] = {}
        self._episodes: dict[int, Episode] = {}
        self._summaries: dict[str, SessionSummary] = {}
        self._seq = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    async def ensure_relation(self, user_id: int, character_id: int) -> Relation:
        for rel in self._relations.values():
            if rel.user_id == int(user_id) and rel.character_id == int(character_id):
                return rel
        ts = now()
        rel = Relation(
            id=self._next(),
            user_id=int(user_id),
            character_id=int(character_id),
            created_at=ts,
            updated_at=ts,
        )
        self._relations[rel.id] = rel
        return rel

    async def get_relation(self, relation_id: int) -> Relation | None:
        return self._relations.get(int(relation_id))

    async def get_by_user_character(self, user_id: int, character_id: int) -> Relation | None:
        for rel in self._relations.values():
            if rel.user_id == int(user_id) and rel.character_id == int(character_id):
                return rel
        return None

    async def update_relation(self, relation_id: int, **fields: Any) -> Relation | None:
        rel = self._relations.get(int(relation_id))
        if rel is None:
            return None
        for key in (
            "user_persona_override",
            "user_nickname",
            "affinity_score",
            "relationship_stage",
            "current_mood",
            "total_turns",
            "last_interaction_at",
            "last_consolidated_at",
        ):
            if key in fields and fields[key] is not None:
                setattr(rel, key, fields[key])
        rel.updated_at = now()
        return rel

    async def touch_relation(self, relation_id: int) -> None:
        rel = self._relations.get(int(relation_id))
        if rel:
            rel.total_turns += 1
            rel.last_interaction_at = now()

    async def add_fact(self, **fields: Any) -> Fact:
        ts = now()
        fact = Fact(
            id=self._next(),
            relation_id=int(fields["relation_id"]),
            category=fields.get("category", "profile"),
            fact_key=fields.get("fact_key", ""),
            fact_key_norm=fields.get("fact_key_norm")
            or fact_key_norm(fields.get("category", "profile"), fields.get("fact_key", "")),
            fact_value=fields["fact_value"],
            confidence=float(fields.get("confidence", 1.0)),
            is_pinned=bool(fields.get("is_pinned", False)),
            status=int(fields.get("status", FACT_ACTIVE)),
            mention_count=int(fields.get("mention_count", 1)),
            content_hash=fields.get("content_hash") or content_hash(fields["fact_value"]),
            source_msg_id=fields.get("source_msg_id"),
            last_confirmed_at=ts,
            created_at=ts,
            updated_at=ts,
        )
        self._facts[fact.id] = fact
        return fact

    async def get_fact(self, fact_id: int) -> Fact | None:
        return self._facts.get(int(fact_id))

    async def find_active_fact(self, relation_id: int, key_norm: str) -> Fact | None:
        for fact in self._facts.values():
            if (
                fact.relation_id == int(relation_id)
                and fact.fact_key_norm == key_norm
                and fact.status == FACT_ACTIVE
            ):
                return fact
        return None

    async def list_facts(self, relation_id: int, include_history: bool = False) -> list[Fact]:
        rows = [f for f in self._facts.values() if f.relation_id == int(relation_id)]
        if not include_history:
            rows = [f for f in rows if f.status in (FACT_ACTIVE, FACT_PENDING)]
        rows.sort(key=lambda f: (f.is_pinned, f.confidence, f.updated_at or now()), reverse=True)
        return rows

    async def update_fact(self, fact_id: int, **fields: Any) -> None:
        fact = self._facts.get(int(fact_id))
        if fact is None:
            return
        for key, value in fields.items():
            if hasattr(fact, key) and value is not None:
                setattr(fact, key, value)
        fact.updated_at = now()

    async def add_episode(self, **fields: Any) -> Episode:
        ts = now()
        episode = Episode(
            id=self._next(),
            relation_id=int(fields["relation_id"]),
            session_id=fields.get("session_id") or "",
            kind=fields.get("kind", "event"),
            summary=fields["summary"],
            emotion_tag=fields.get("emotion_tag") or "",
            importance=int(fields.get("importance", 3)),
            status=int(fields.get("status", EP_ACTIVE)),
            is_pinned=bool(fields.get("is_pinned", False)),
            happened_at=fields.get("happened_at") or ts,
            source_msg_id=fields.get("source_msg_id"),
            embedding=list(fields.get("embedding") or []),
            created_at=ts,
        )
        self._episodes[episode.id] = episode
        return episode

    async def get_episode(self, episode_id: int) -> Episode | None:
        return self._episodes.get(int(episode_id))

    async def list_episodes(
        self, relation_id: int, include_archived: bool = False, limit: int = 200
    ) -> list[Episode]:
        rows = [e for e in self._episodes.values() if e.relation_id == int(relation_id)]
        if not include_archived:
            rows = [e for e in rows if e.status == EP_ACTIVE]
        rows.sort(key=lambda e: e.happened_at or now(), reverse=True)
        return rows[:limit]

    async def update_episode(self, episode_id: int, **fields: Any) -> None:
        episode = self._episodes.get(int(episode_id))
        if episode is None:
            return
        for key, value in fields.items():
            if hasattr(episode, key) and value is not None:
                setattr(episode, key, value)

    async def archive_episodes(self, stale_before: datetime, max_importance: int = 3) -> int:
        count = 0
        for episode in self._episodes.values():
            if episode.status != EP_ACTIVE or episode.is_pinned:
                continue
            if episode.importance > max_importance:
                continue
            reference = episode.last_accessed_at or episode.happened_at
            if reference and reference < stale_before:
                episode.status = EP_ARCHIVED
                count += 1
        return count

    async def get_summary(self, session_id: str) -> SessionSummary | None:
        return self._summaries.get(session_id)

    async def save_summary(self, summary: SessionSummary) -> None:
        summary.updated_at = now()
        self._summaries[summary.session_id] = summary

    async def list_relations(self, user_id: int | None = None) -> list[Relation]:
        rows = list(self._relations.values())
        if user_id is not None:
            rows = [r for r in rows if r.user_id == int(user_id)]
        return rows

    async def archive_pending_facts(self, stale_before: datetime) -> int:
        count = 0
        for fact in self._facts.values():
            if fact.status == FACT_PENDING and (fact.created_at or datetime.now(timezone.utc)) < stale_before:
                fact.status = FACT_ARCHIVED
                count += 1
        return count

    async def decay_affinity(self, per_day: int = 1) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        count = 0
        for rel in self._relations.values():
            if rel.affinity_score > 0 and rel.last_interaction_at and rel.last_interaction_at < cutoff:
                rel.affinity_score = max(0, rel.affinity_score - per_day)
                count += 1
        return count

    async def clear_relation_memories(self, relation_id: int) -> int:
        count = 0
        for fact in list(self._facts.values()):
            if fact.relation_id == int(relation_id):
                self._facts.pop(fact.id, None)
                count += 1
        for episode in list(self._episodes.values()):
            if episode.relation_id == int(relation_id):
                self._episodes.pop(episode.id, None)
                count += 1
        return count

    async def clear_session_memories(self, session_id: str) -> int:
        count = 0
        for episode in list(self._episodes.values()):
            if episode.session_id == session_id and episode.kind != "memoir":
                self._episodes.pop(episode.id, None)
                count += 1
        self._summaries.pop(session_id, None)
        return count


# ---------------- PostgreSQL ----------------


class PostgresRelationStore(RelationStore):
    def __init__(self, engine: AsyncEngine) -> None:
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    async def ensure_relation(self, user_id: int, character_id: int) -> Relation:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(RelationRow).where(
                        RelationRow.user_id == int(user_id),
                        RelationRow.character_id == int(character_id),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = RelationRow(user_id=int(user_id), character_id=int(character_id))
                session.add(row)
                await session.commit()
            return _relation(row)

    async def get_relation(self, relation_id: int) -> Relation | None:
        async with self._factory() as session:
            row = await session.get(RelationRow, int(relation_id))
        return _relation(row) if row else None

    async def get_by_user_character(self, user_id: int, character_id: int) -> Relation | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(RelationRow).where(
                        RelationRow.user_id == int(user_id),
                        RelationRow.character_id == int(character_id),
                    )
                )
            ).scalar_one_or_none()
        return _relation(row) if row else None

    async def update_relation(self, relation_id: int, **fields: Any) -> Relation | None:
        async with self._factory() as session:
            row = await session.get(RelationRow, int(relation_id))
            if row is None:
                return None
            for key in (
                "user_persona_override",
                "user_nickname",
                "affinity_score",
                "relationship_stage",
                "current_mood",
                "total_turns",
                "last_interaction_at",
                "last_consolidated_at",
            ):
                if key in fields and fields[key] is not None:
                    setattr(row, key, fields[key])
            row.updated_at = now()
            await session.commit()
            return _relation(row)

    async def touch_relation(self, relation_id: int) -> None:
        async with self._factory() as session:
            row = await session.get(RelationRow, int(relation_id))
            if row is not None:
                row.total_turns = int(row.total_turns or 0) + 1
                row.last_interaction_at = now()
                row.updated_at = now()
                await session.commit()

    async def add_fact(self, **fields: Any) -> Fact:
        async with self._factory() as session:
            row = FactRow(
                relation_id=int(fields["relation_id"]),
                category=fields.get("category", "profile")[:16],
                fact_key=(fields.get("fact_key") or "")[:64],
                fact_key_norm=(
                    fields.get("fact_key_norm")
                    or fact_key_norm(fields.get("category", "profile"), fields.get("fact_key", ""))
                )[:64],
                fact_value=fields["fact_value"][:255],
                confidence=float(fields.get("confidence", 1.0)),
                is_pinned=bool(fields.get("is_pinned", False)),
                status=int(fields.get("status", FACT_ACTIVE)),
                mention_count=int(fields.get("mention_count", 1)),
                content_hash=fields.get("content_hash") or content_hash(fields["fact_value"]),
                source_msg_id=fields.get("source_msg_id"),
                last_confirmed_at=now(),
            )
            session.add(row)
            await session.commit()
            return _fact(row)

    async def get_fact(self, fact_id: int) -> Fact | None:
        async with self._factory() as session:
            row = await session.get(FactRow, int(fact_id))
        return _fact(row) if row else None

    async def find_active_fact(self, relation_id: int, key_norm: str) -> Fact | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(FactRow).where(
                        FactRow.relation_id == int(relation_id),
                        FactRow.fact_key_norm == key_norm,
                        FactRow.status == FACT_ACTIVE,
                    )
                )
            ).scalar_one_or_none()
        return _fact(row) if row else None

    async def list_facts(self, relation_id: int, include_history: bool = False) -> list[Fact]:
        async with self._factory() as session:
            stmt = select(FactRow).where(FactRow.relation_id == int(relation_id))
            if not include_history:
                stmt = stmt.where(FactRow.status.in_((FACT_ACTIVE, FACT_PENDING)))
            rows = (await session.execute(stmt)).scalars().all()
        rows.sort(key=lambda r: (bool(r.is_pinned), r.confidence, r.updated_at), reverse=True)
        return [_fact(r) for r in rows]

    async def update_fact(self, fact_id: int, **fields: Any) -> None:
        async with self._factory() as session:
            row = await session.get(FactRow, int(fact_id))
            if row is None:
                return
            for key, value in fields.items():
                if hasattr(row, key) and value is not None:
                    setattr(row, key, value)
            row.updated_at = now()
            await session.commit()

    async def add_episode(self, **fields: Any) -> Episode:
        async with self._factory() as session:
            row = EpisodeRow(
                relation_id=int(fields["relation_id"]),
                session_id=(fields.get("session_id") or "")[:64] or None,
                kind=fields.get("kind", "event")[:16],
                summary=fields["summary"],
                emotion_tag=(fields.get("emotion_tag") or "")[:16] or None,
                importance=int(fields.get("importance", 3)),
                status=int(fields.get("status", EP_ACTIVE)),
                is_pinned=bool(fields.get("is_pinned", False)),
                happened_at=fields.get("happened_at") or now(),
                source_msg_id=fields.get("source_msg_id"),
                content_hash=fields.get("content_hash") or content_hash(fields["summary"]),
                embedding=list(fields.get("embedding") or []),
            )
            session.add(row)
            await session.commit()
            return _episode(row)

    async def get_episode(self, episode_id: int) -> Episode | None:
        async with self._factory() as session:
            row = await session.get(EpisodeRow, int(episode_id))
        return _episode(row) if row else None

    async def list_episodes(
        self, relation_id: int, include_archived: bool = False, limit: int = 200
    ) -> list[Episode]:
        async with self._factory() as session:
            stmt = select(EpisodeRow).where(EpisodeRow.relation_id == int(relation_id))
            if not include_archived:
                stmt = stmt.where(EpisodeRow.status == EP_ACTIVE)
            stmt = stmt.order_by(EpisodeRow.happened_at.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
        return [_episode(r) for r in rows]

    async def update_episode(self, episode_id: int, **fields: Any) -> None:
        async with self._factory() as session:
            row = await session.get(EpisodeRow, int(episode_id))
            if row is None:
                return
            for key, value in fields.items():
                if hasattr(row, key) and value is not None:
                    setattr(row, key, value)
            await session.commit()

    async def archive_episodes(self, stale_before: datetime, max_importance: int = 3) -> int:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(EpisodeRow).where(
                        EpisodeRow.status == EP_ACTIVE,
                        EpisodeRow.is_pinned.is_(False),
                        EpisodeRow.importance <= int(max_importance),
                    )
                )
            ).scalars().all()
            count = 0
            for row in rows:
                reference = row.last_accessed_at or row.happened_at
                if reference and reference < stale_before:
                    row.status = EP_ARCHIVED
                    count += 1
            await session.commit()
            return count

    async def get_summary(self, session_id: str) -> SessionSummary | None:
        async with self._factory() as session:
            row = await session.get(SummaryRow, session_id)
        if row is None:
            return None
        return SessionSummary(
            session_id=row.session_id,
            summary=row.summary,
            covered_until_msg_id=row.covered_until_msg_id,
            covered_turns=int(row.covered_turns or 0),
            token_estimate=int(row.token_estimate or 0),
            updated_at=row.updated_at,
        )

    async def save_summary(self, summary: SessionSummary) -> None:
        async with self._factory() as session:
            row = await session.get(SummaryRow, summary.session_id)
            if row is None:
                row = SummaryRow(session_id=summary.session_id, summary=summary.summary)
                session.add(row)
            row.summary = summary.summary
            row.covered_until_msg_id = summary.covered_until_msg_id
            row.covered_turns = summary.covered_turns
            row.token_estimate = summary.token_estimate
            row.updated_at = now()
            await session.commit()

    async def list_relations(self, user_id: int | None = None) -> list[Relation]:
        async with self._factory() as session:
            stmt = select(RelationRow)
            if user_id is not None:
                stmt = stmt.where(RelationRow.user_id == int(user_id))
            rows = (await session.execute(stmt)).scalars().all()
        return [_relation(r) for r in rows]

    async def archive_pending_facts(self, stale_before: datetime) -> int:
        async with self._factory() as session:
            result = await session.execute(
                update(FactRow)
                .where(FactRow.status == FACT_PENDING, FactRow.created_at < stale_before)
                .values(status=FACT_ARCHIVED, updated_at=now())
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def decay_affinity(self, per_day: int = 1) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        async with self._factory() as session:
            result = await session.execute(
                update(RelationRow)
                .where(
                    RelationRow.affinity_score > 0,
                    RelationRow.last_interaction_at.is_not(None),
                    RelationRow.last_interaction_at < cutoff,
                )
                .values(
                    affinity_score=func.greatest(0, RelationRow.affinity_score - int(per_day)),
                    updated_at=now(),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def clear_relation_memories(self, relation_id: int) -> int:
        async with self._factory() as session:
            facts = await session.execute(
                delete(FactRow).where(FactRow.relation_id == int(relation_id))
            )
            episodes = await session.execute(
                delete(EpisodeRow).where(EpisodeRow.relation_id == int(relation_id))
            )
            await session.commit()
            return int((facts.rowcount or 0) + (episodes.rowcount or 0))

    async def clear_session_memories(self, session_id: str) -> int:
        async with self._factory() as session:
            result = await session.execute(
                delete(EpisodeRow).where(
                    EpisodeRow.session_id == session_id, EpisodeRow.kind != "memoir"
                )
            )
            await session.execute(delete(SummaryRow).where(SummaryRow.session_id == session_id))
            await session.commit()
            return int(result.rowcount or 0)


def _relation(row: RelationRow) -> Relation:
    return Relation(
        id=int(row.id),
        user_id=int(row.user_id),
        character_id=int(row.character_id),
        user_persona_override=row.user_persona_override or "",
        user_nickname=row.user_nickname or "",
        affinity_score=int(row.affinity_score or 0),
        relationship_stage=row.relationship_stage or "stranger",
        current_mood=row.current_mood or "neutral",
        total_turns=int(row.total_turns or 0),
        last_interaction_at=row.last_interaction_at,
        last_consolidated_at=row.last_consolidated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _fact(row: FactRow) -> Fact:
    return Fact(
        id=int(row.id),
        relation_id=int(row.relation_id),
        category=row.category,
        fact_key=row.fact_key,
        fact_key_norm=row.fact_key_norm,
        fact_value=row.fact_value,
        confidence=float(row.confidence or 1.0),
        is_pinned=bool(row.is_pinned),
        status=int(row.status),
        superseded_by=int(row.superseded_by) if row.superseded_by else None,
        mention_count=int(row.mention_count or 1),
        source_msg_id=int(row.source_msg_id) if row.source_msg_id else None,
        last_confirmed_at=row.last_confirmed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _episode(row: EpisodeRow) -> Episode:
    return Episode(
        id=int(row.id),
        relation_id=int(row.relation_id),
        session_id=row.session_id or "",
        kind=row.kind,
        summary=row.summary,
        emotion_tag=row.emotion_tag or "",
        importance=int(row.importance or 3),
        status=int(row.status),
        is_pinned=bool(row.is_pinned),
        happened_at=row.happened_at,
        last_accessed_at=row.last_accessed_at,
        access_count=int(row.access_count or 0),
        embedding=list(row.embedding or []),
        source_msg_id=int(row.source_msg_id) if row.source_msg_id else None,
        created_at=row.created_at,
    )


def create_relation_store(repo: Any) -> RelationStore:
    if isinstance(repo, PostgresRepository):
        return PostgresRelationStore(repo.engine)
    return InMemoryRelationStore()


__all__ = [
    "RelationStore",
    "InMemoryRelationStore",
    "PostgresRelationStore",
    "create_relation_store",
]
