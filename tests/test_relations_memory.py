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

class _FakeEmbedder:
    async def embed(self, texts, is_query=False):
        return [[1.0, 0.0] if "加班" in t or is_query else [0.0, 1.0] for t in texts]


def test_semantic_conflict_across_different_keys():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        service, store = _service(repo)
        relation = await service.relation_for_session(1, "s1")
        await service.remember(
            relation.id,
            "s1",
            facts=[{"category": "preference", "key": "food.spicy", "value": "爱吃辣"}],
        )
        # 不同 key、不同措辞，但语义冲突
        await service.remember(
            relation.id,
            "s1",
            facts=[{"category": "preference", "key": "health.stomach", "value": "胃不好，现在戒辣了"}],
        )
        active = await store.list_facts(relation.id)
        assert len(active) == 1
        assert "戒辣" in active[0].fact_value
        assert "曾经" in active[0].fact_value
        history = await store.list_facts(relation.id, include_history=True)
        assert len(history) == 2

    asyncio.run(run())


def test_recall_respects_token_budget():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        store = InMemoryRelationStore()
        ctx = SimpleNamespace(
            repo=repo,
            settings=SimpleNamespace(memory_top_k=5, memory_budget_tokens=18, memory_facts_max=8),
        )
        service = MemoryService(ctx, store)
        relation = await service.relation_for_session(1, "s1")
        for i in range(5):
            await store.add_fact(
                relation_id=relation.id,
                category="profile",
                fact_key=f"k{i}",
                fact_value=f"这是一条比较长的记忆内容{i}",
            )
        lines = await service.recall(relation.id, "s1", "你好")
        assert sum(len(x) for x in lines) <= 18 + 1

    asyncio.run(run())


def test_recall_uses_vector_relevance():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        store = InMemoryRelationStore()
        ctx = SimpleNamespace(
            repo=repo,
            embedder=_FakeEmbedder(),
            settings=SimpleNamespace(memory_top_k=5, memory_episodes_max=3),
        )
        service = MemoryService(ctx, store)
        relation = await service.relation_for_session(1, "s1")
        await store.add_episode(
            relation_id=relation.id, session_id="s1", summary="无关的琐事", importance=5,
            embedding=[0.0, 1.0],
        )
        await store.add_episode(
            relation_id=relation.id, session_id="s1", summary="你加班到很晚", importance=5,
            embedding=[1.0, 0.0],
        )
        lines = await service.recall(relation.id, "s1", "今天加班好累")
        assert lines and "加班" in lines[0]

    asyncio.run(run())


def test_affinity_decay_per_day():
    async def run():
        repo = InMemoryRepository()
        await _session(repo)
        service, store = _service(repo)
        relation = await service.relation_for_session(1, "s1")
        old = datetime.now(timezone.utc) - timedelta(days=3)
        await store.update_relation(relation.id, affinity_score=10, last_interaction_at=old)
        for _ in range(3):
            await service.affinity_decay(1)
        refreshed = await store.get_relation(relation.id)
        assert refreshed.affinity_score == 7

    asyncio.run(run())
