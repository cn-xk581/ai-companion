"""羁绊记忆：冲突覆盖 / 强化 / 遗忘 / 隔离 / 分组。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain.companion import Persona
from app.domain.relations import SessionSummary
from app.services.memory_service import MemoryService
from app.storage.postgres import InMemoryRepository, SessionRecord
from app.storage.relation_store import InMemoryRelationStore


def _ctx(repo):
    return SimpleNamespace(repo=repo, settings=SimpleNamespace(memory_top_k=5))


async def _session(repo, sid="s1", user_id=1, character_id=101):
    record = SessionRecord(session_id=sid, user_id=user_id, persona=Persona(name="夷光"))
    record.character_id = character_id
    await repo.save_session(record)
    return record


def _service(repo):
    store = InMemoryRelationStore()
    return MemoryService(_ctx(repo), store), store


def test_conflict_supersedes_old_fact_and_keeps_history():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        service, store = _service(repo)
        relation = await service.relation_for_session(1, "s1")
        assert relation is not None

        first = await service.remember(
            relation.id,
            "s1",
            facts=[{"category": "preference", "key": "food.spicy", "value": "爱吃辣"}],
        )
        assert first["facts"] == 1

        second = await service.remember(
            relation.id,
            "s1",
            facts=[
                {
                    "category": "preference",
                    "key": "food.spicy",
                    "value": "因胃不好已经戒辣",
                    "merged": "曾经无辣不欢，但近期因胃不好已严格戒辣",
                }
            ],
        )
        assert second["facts"] == 1

        active = await store.find_active_fact(relation.id, "preference.food.spicy")
        assert active is not None and "戒辣" in active.fact_value
        history = await store.list_facts(relation.id, include_history=True)
        assert len(history) == 2
        old = next(f for f in history if f.id != active.id)
        assert old.status == 2 and old.superseded_by == active.id

        lines = await service.recall(relation.id, "s1", "晚上吃什么")
        assert any("戒辣" in line for line in lines)
        assert all("爱吃辣" not in line for line in lines)

    asyncio.run(run())


def test_same_value_strengthens_instead_of_duplicating():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        service, store = _service(repo)
        relation = await service.relation_for_session(1, "s1")
        item = {"category": "profile", "key": "pet", "value": "养了一只叫汤圆的猫"}
        await service.remember(relation.id, "s1", facts=[item])
        result = await service.remember(relation.id, "s1", facts=[item])
        assert result["skipped"] == 1
        facts = await store.list_facts(relation.id)
        assert len(facts) == 1
        assert facts[0].mention_count == 2

    asyncio.run(run())


def test_decay_archive_respects_pinned_and_importance():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        service, store = _service(repo)
        relation = await service.relation_for_session(1, "s1")
        old = datetime.now(timezone.utc) - timedelta(days=30)

        await store.add_episode(
            relation_id=relation.id, session_id="s1", kind="event",
            summary="上周二吃了黄焖鸡", importance=2, happened_at=old,
        )
        await store.add_episode(
            relation_id=relation.id, session_id="s1", kind="milestone",
            summary="第一次一起看星星", importance=9, happened_at=old,
        )
        await store.add_episode(
            relation_id=relation.id, session_id="s1", kind="event",
            summary="用户置顶的约定", importance=2, happened_at=old, is_pinned=True,
        )
        archived = await service.archive_daily(stale_days=14, max_importance=3)
        assert archived == 1
        remaining = {e.summary for e in await store.list_episodes(relation.id)}
        assert "上周二吃了黄焖鸡" not in remaining
        assert "第一次一起看星星" in remaining
        assert "用户置顶的约定" in remaining

    asyncio.run(run())


def test_relation_memory_is_per_user():
    async def run():
        repo = InMemoryRepository()
        await _session(repo, sid="s1", user_id=1)
        await _session(repo, sid="s2", user_id=2)
        service, store = _service(repo)
        r1 = await service.relation_for_session(1, "s1")
        r2 = await service.relation_for_session(2, "s2")
        assert r1.id != r2.id
        await service.remember(
            r1.id, "s1", facts=[{"category": "profile", "key": "birthday", "value": "3 月 2 日"}]
        )
        assert await store.list_facts(r2.id) == []
        assert len(await store.list_facts(r1.id)) == 1

    asyncio.run(run())


def test_grouped_view_has_three_sections():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        service, _ = _service(repo)
        relation = await service.relation_for_session(1, "s1")
        await service.remember(
            relation.id, "s1",
            facts=[{"category": "taboo", "key": "allergy", "value": "芒果过敏"}],
            episodes=[{"kind": "event", "summary": "一起淋雨", "importance": 6}],
        )
        await service.store.save_summary(
            SimpleNamespace(session_id="s1") and __import__("app.domain.relations", fromlist=["SessionSummary"]).SessionSummary(
                session_id="s1", summary="上次聊到神祠"
            )
        )
        data = await service.grouped(relation.id, "s1")
        assert data["facts"] and data["episodes"] and data["summary"] == "上次聊到神祠"

    asyncio.run(run())
