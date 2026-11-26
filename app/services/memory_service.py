"""羁绊记忆服务：召回、写入、冲突覆盖、遗忘归档。"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.relations import (
    EP_ACTIVE,
    FACT_ACTIVE,
    FACT_PENDING,
    Episode,
    Fact,
    content_hash,
    fact_key_norm,
    now,
    retention_score,
)
from app.storage.relation_store import RelationStore

logger = logging.getLogger(__name__)

# 从旧抽取格式迁移时的类别映射
def _char_set(text: str) -> set[str]:
    cleaned = re.sub(r"[\s，。！？、,.!?；;：:（）()\"']", "", text or "")
    return set(cleaned)


def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", text or "")
    return {cleaned[i : i + 2] for i in range(max(0, len(cleaned) - 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _negated(text: str) -> bool:
    return bool(re.search(r"不|没|戒|停|取消|拒绝|再也不", text or ""))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_FACT_CATEGORIES = {"fact", "preference", "anniversary", "profile", "taboo", "social"}
_EVENT_CATEGORIES = {"event", "story"}


class MemoryService:
    def __init__(self, ctx, store: RelationStore) -> None:
        self.ctx = ctx
        self.store = store

    # ---------------- 关系 ----------------

    async def relation_for_session(self, user_id: int, session_id: str):
        session = await self.ctx.repo.get_session(session_id)
        if session is None or session.user_id != user_id or not session.character_id:
            return None
        return await self.store.ensure_relation(user_id, session.character_id)

    async def relation_for_character(self, user_id: int, character_id: int):
        return await self.store.ensure_relation(user_id, character_id)

    # ---------------- 召回 ----------------

    async def recall_for_session(
        self, user_id: int, session_id: str, query: str, limit: int = 12
    ) -> list[str]:
        relation = await self.relation_for_session(user_id, session_id)
        if relation is None:
            return []
        return await self.recall(relation.id, session_id, query, limit=limit)

    async def recall(self, relation_id: int, session_id: str, query: str, limit: int = 12) -> list[str]:
        lines: list[str] = []
        summary = await self.store.get_summary(session_id)
        if summary and summary.summary:
            lines.append(f"上次聊到：{summary.summary[:240]}")

        facts = await self.store.list_facts(relation_id)
        facts = [f for f in facts if f.status in (FACT_ACTIVE, FACT_PENDING)]
        facts.sort(key=lambda f: (f.is_pinned, f.status == FACT_ACTIVE, f.confidence), reverse=True)
        for fact in facts[: self._facts_max]:
            lines.append(fact.fact_value)

        query_vec = await self._embed_query(query)
        episodes = await self.store.list_episodes(relation_id, limit=80)
        scored = []
        for ep in episodes:
            relevance = _cosine(query_vec, ep.embedding) if query_vec and ep.embedding else 0.0
            score = retention_score(
                importance=ep.importance,
                pinned=ep.is_pinned,
                last_accessed_at=ep.last_accessed_at,
                happened_at=ep.happened_at,
                relevance=relevance,
            )
            scored.append((score, ep))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        used = [ep for _, ep in scored[: self._episodes_max] if ep]
        for ep in used:
            tag = f"（{ep.emotion_tag}）" if ep.emotion_tag else ""
            lines.append(f"{ep.summary}{tag}")
        if used:
            await self._touch_episodes([ep.id for ep in used])
        return self._fit_budget(lines)[:limit]

    @property
    def _facts_max(self) -> int:
        return int(getattr(self.ctx.settings, "memory_facts_max", 8))

    @property
    def _episodes_max(self) -> int:
        return int(getattr(self.ctx.settings, "memory_episodes_max", 3))

    def _fit_budget(self, lines: list[str]) -> list[str]:
        budget = int(getattr(self.ctx.settings, "memory_budget_tokens", 600))
        kept: list[str] = []
        used = 0
        for line in lines:
            cost = max(1, len(line))
            if kept and used + cost > budget:
                break
            kept.append(line)
            used += cost
        return kept

    async def _embed_query(self, text: str) -> list[float]:
        embedder = getattr(self.ctx, "embedder", None)
        if embedder is None:
            return []
        try:
            vectors = await embedder.embed([text], is_query=True)
            return list(vectors[0]) if vectors else []
        except Exception:  # noqa: BLE001
            return []

    async def _touch_episodes(self, ids: list[int]) -> None:
        for ep_id in ids:
            episode = await self.store.get_episode(ep_id)
            if episode is not None:
                await self.store.update_episode(
                    ep_id, last_accessed_at=now(), access_count=episode.access_count + 1
                )

    # ---------------- 写入 ----------------

    async def remember_from_items(
        self, user_id: int, session_id: str, items: list[dict[str, Any]]
    ) -> dict[str, int]:
        relation = await self.relation_for_session(user_id, session_id)
        if relation is None or not items:
            return {"facts": 0, "episodes": 0, "skipped": len(items or [])}
        facts: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        for item in items:
            content = str(item.get("content") or "").strip()
            if len(content) < 2:
                continue
            category = str(item.get("category") or "fact")
            importance = float(item.get("importance", 0.5))
            if category in _FACT_CATEGORIES:
                facts.append(
                    {
                        "category": category if category in ("profile", "preference", "social", "taboo") else "profile",
                        "key": content[:40],
                        "value": content,
                        "confidence": 0.55 + 0.45 * max(0.0, min(importance, 1.0)),
                    }
                )
            elif category in _EVENT_CATEGORIES:
                episodes.append(
                    {
                        "kind": "event",
                        "summary": content,
                        "importance": max(1, min(10, round(importance * 10) or 3)),
                        "emotion_tag": "",
                    }
                )
            else:
                episodes.append(
                    {
                        "kind": "thread",
                        "summary": content,
                        "importance": max(1, min(10, round(importance * 10) or 3)),
                    }
                )
        return await self.remember(relation.id, session_id, facts=facts, episodes=episodes)

    async def remember(
        self,
        relation_id: int,
        session_id: str,
        *,
        facts: list[dict[str, Any]] | None = None,
        episodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        saved_facts = 0
        skipped = 0
        for item in facts or []:
            value = str(item.get("value") or item.get("fact_value") or "").strip()
            if not value:
                continue
            category = str(item.get("category") or "profile")
            key = str(item.get("key") or item.get("fact_key") or value)[:64]
            key_norm = fact_key_norm(category, key)
            existing = await self.store.find_active_fact(relation_id, key_norm)
            if existing is not None:
                if content_hash(existing.fact_value) == content_hash(value):
                    await self.store.update_fact(
                        existing.id,
                        mention_count=existing.mention_count + 1,
                        last_confirmed_at=now(),
                        confidence=min(1.0, existing.confidence + 0.05),
                    )
                    skipped += 1
                    continue
                # 冲突：旧值归档，新值接管（软删除，可追溯）
                merged = str(item.get("merged") or "")
                if not merged:
                    negative = value.startswith(("不", "没", "没有", "不再", "戒", "取消"))
                    merged = (
                        f"曾经{existing.fact_value}，现在{value}"
                        if not negative
                        else f"曾经{existing.fact_value}；现在{value}（已更正）"
                    )
                new_fact = await self.store.add_fact(
                    relation_id=relation_id,
                    category=category,
                    fact_key=key,
                    fact_key_norm=key_norm,
                    fact_value=merged,
                    confidence=float(item.get("confidence", 0.9)),
                    status=FACT_ACTIVE,
                    source_msg_id=item.get("source_msg_id"),
                )
                await self.store.update_fact(
                    existing.id, status=2, superseded_by=new_fact.id
                )
                saved_facts += 1
                continue
            conflict = await self._semantic_conflict(relation_id, category, value)
            if conflict is not None:
                old_fact, merged = conflict
                new_fact = await self.store.add_fact(
                    relation_id=relation_id,
                    category=category,
                    fact_key=key,
                    fact_key_norm=key_norm,
                    fact_value=merged,
                    confidence=float(item.get("confidence", 0.85)),
                    status=FACT_ACTIVE,
                    source_msg_id=item.get("source_msg_id"),
                )
                await self.store.update_fact(old_fact.id, status=2, superseded_by=new_fact.id)
                saved_facts += 1
                continue
            await self.store.add_fact(
                relation_id=relation_id,
                category=category,
                fact_key=key,
                fact_key_norm=key_norm,
                fact_value=value,
                confidence=float(item.get("confidence", 0.8)),
                status=int(item.get("status", FACT_ACTIVE)),
                is_pinned=bool(item.get("is_pinned", False)),
                source_msg_id=item.get("source_msg_id"),
            )
            saved_facts += 1

        saved_episodes = 0
        episode_items = [e for e in (episodes or []) if len(str(e.get("summary") or "").strip()) >= 2]
        embeddings: list[list[float]] = []
        embedder = getattr(self.ctx, "embedder", None)
        if embedder is not None and episode_items:
            try:
                embeddings = await embedder.embed([str(e["summary"]) for e in episode_items])
            except Exception:  # noqa: BLE001
                embeddings = []
        for idx, item in enumerate(episode_items):
            await self.store.add_episode(
                relation_id=relation_id,
                session_id=session_id,
                kind=str(item.get("kind") or "event"),
                summary=str(item["summary"]).strip(),
                emotion_tag=str(item.get("emotion_tag") or ""),
                importance=int(item.get("importance", 3)),
                happened_at=item.get("happened_at") or now(),
                source_msg_id=item.get("source_msg_id"),
                embedding=embeddings[idx] if idx < len(embeddings) else [],
            )
            saved_episodes += 1
        return {"facts": saved_facts, "episodes": saved_episodes, "skipped": skipped}

    async def summarize_session(self, user_id: int, session_id: str) -> str | None:
        """把最近 40 条对话压成摘要；不足 8 条或 LLM 失败则跳过。"""
        relation = await self.relation_for_session(user_id, session_id)
        if relation is None:
            return None
        messages = await self.ctx.repo.recent_messages(session_id, 40)
        if len(messages) < 8:
            return None
        from app.llm.prompts import build_summary_messages

        transcript = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        try:
            result = await self.ctx.llm.complete(
                build_summary_messages(transcript), temperature=0.0, max_tokens=300
            )
            text = (result.content or "").strip()
        except Exception as exc:  # noqa: BLE001 - 摘要失败不阻断
            logger.warning("[memory] 摘要失败：%s", exc)
            return None
        if not text:
            return None
        from app.domain.relations import SessionSummary

        await self.store.save_summary(
            SessionSummary(
                session_id=session_id,
                summary=text[:1200],
                covered_turns=len(transcript),
                token_estimate=len(text),
            )
        )
        return text

    async def maybe_summarize(self, user_id: int, session_id: str, every: int = 12) -> None:
        relation = await self.relation_for_session(user_id, session_id)
        if relation is None or relation.total_turns == 0:
            return
        if relation.total_turns % every != 0:
            return
        await self.summarize_session(user_id, session_id)

    async def sync_relation_state(
        self, user_id: int, session_id: str, score: float, stage: str, mood: str
    ) -> None:
        relation = await self.relation_for_session(user_id, session_id)
        if relation is None:
            return
        await self.store.update_relation(
            relation.id,
            affinity_score=max(0, min(100, int(round(score)))),
            relationship_stage=stage,
            current_mood=mood,
        )
        await self.store.touch_relation(relation.id)

    async def _semantic_conflict(self, relation_id: int, category: str, value: str):
        """不同 key 的语义冲突：同类别 + 关键词重叠 + 否定翻转 → 合并覆盖。"""
        facts = await self.store.list_facts(relation_id)
        candidate_tokens = _char_set(value)
        best = None
        best_sim = 0.0
        for fact in facts:
            if fact.status != FACT_ACTIVE or fact.category != category:
                continue
            sim = _jaccard(candidate_tokens, _char_set(fact.fact_value))
            if sim > best_sim:
                best, best_sim = fact, sim
        if best is None or best_sim < 0.1:
            return None
        if _negated(best.fact_value) == _negated(value):
            return None
        merged = f"曾经{best.fact_value}，现在{value}"
        judged = await self._judge_fact(best, value)
        if judged is not None:
            action = judged.get("action")
            if action == "NOOP":
                return None
            if action == "DELETE":
                return best, f"{best.fact_value}（用户已否认）"
            merged = str(judged.get("merged_value") or merged)
        return best, merged

    async def _judge_fact(self, old: Fact, new_value: str) -> dict | None:
        if not bool(getattr(self.ctx.settings, "memory_judge_enabled", False)):
            return None
        llm = getattr(self.ctx, "llm", None)
        if llm is None:
            return None
        prompt = (
            "你是记忆管理专家。判断新信息是否推翻/修正旧记忆。"
            "只输出 JSON：{\"action\":\"UPDATE|DELETE|NOOP\",\"merged_value\":\"...\"}。"
            "UPDATE 时 merged_value 要保留时间线（曾经…现在…）；"
            "如果只是玩笑或没有冲突，返回 NOOP。"
        )
        user = f"旧记忆：{old.fact_value}\n新信息：{new_value}"
        try:
            result = await llm.complete(
                [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                temperature=0.0,
                max_tokens=200,
            )
            data = json.loads((result.content or "").strip().strip("`").removeprefix("json"))
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    async def affinity_decay(self, per_day: int | None = None) -> int:
        days = int(getattr(self.ctx.settings, "affinity_decay_per_day", 1)) if per_day is None else per_day
        return await self.store.decay_affinity(days)

    # ---------------- 归档 / 遗忘 ----------------

    async def archive_daily(self, stale_days: int = 14, max_importance: int = 3) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        return await self.store.archive_episodes(cutoff, max_importance=max_importance)

    async def grouped(self, relation_id: int, session_id: str) -> dict[str, Any]:
        facts = await self.store.list_facts(relation_id)
        episodes = await self.store.list_episodes(relation_id, limit=100)
        summary = await self.store.get_summary(session_id)
        return {
            "facts": [
                {
                    "id": f.id,
                    "category": f.category,
                    "content": f.fact_value,
                    "confidence": round(f.confidence, 2),
                    "pinned": f.is_pinned,
                    "mention_count": f.mention_count,
                    "updated_at": f.updated_at.isoformat() if f.updated_at else None,
                }
                for f in facts
            ],
            "episodes": [
                {
                    "id": e.id,
                    "session_id": e.session_id,
                    "kind": e.kind,
                    "summary": e.summary,
                    "emotion_tag": e.emotion_tag,
                    "importance": e.importance,
                    "pinned": e.is_pinned,
                    "happened_at": e.happened_at.isoformat() if e.happened_at else None,
                }
                for e in episodes
            ],
            "summary": summary.summary if summary else "",
        }
