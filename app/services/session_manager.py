"""会话调度层：组装 AgentState -> 调用 graph -> 返回结果。

同时负责：
- 会话快照加载（Redis 缓存优先，冷启动回源 PostgreSQL）；
- **会话归属校验**：每个 session 强绑定 user_id，越权访问抛 SessionOwnershipError；
- 记忆管理（list/delete）、会话重置、人设更新（均校验归属）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.graph.builder import build_graph
from app.graph.context import GraphContext
from app.graph.state import AgentState, ChatMessage
from app.persona.models import MoodState, Persona, RelationshipStage
from app.storage.postgres import SessionSnapshot

logger = logging.getLogger(__name__)


class SessionOwnershipError(Exception):
    """会话归属校验失败（越权访问）。"""


class SessionNotFoundError(Exception):
    """会话不存在（只读/管理类操作不应自动创建会话）。"""


def _get(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


class SessionManager:
    def __init__(self, ctx: GraphContext) -> None:
        self.ctx = ctx
        self.graph = build_graph(ctx)

    # ---------------- 状态装配（chat 与 stream 共用） ----------------

    async def build_state(
        self, session_id: str, user_input: str, user_id: str, client_msg_id: str | None = None
    ) -> AgentState:
        session_id = session_id or uuid.uuid4().hex
        snapshot = await self._load_snapshot(session_id, user_id)
        history = await self.ctx.repo.get_recent_chat(session_id, self.ctx.settings.history_window)
        return AgentState(
            session_id=session_id,
            user_id=user_id,
            user_input=user_input,
            client_msg_id=client_msg_id,
            persona=snapshot.persona,
            relationship_stage=snapshot.relationship_stage,
            relationship_score=snapshot.relationship_score,
            mood=snapshot.mood,
            chat_history=history,
        )

    def format_result(self, state: AgentState, session_id: str) -> dict:
        stage = _get(state, "relationship_stage", RelationshipStage.STRANGER)
        stage_value = stage.value if isinstance(stage, RelationshipStage) else str(stage)
        mood = _get(state, "mood")
        mood_name = mood.current_mood if isinstance(mood, MoodState) else str(mood)
        mood_intensity = mood.mood_intensity if isinstance(mood, MoodState) else 0.5
        return {
            "session_id": session_id,
            "reply": _get(state, "agent_reply") or "",
            "relationship_stage": stage_value,
            "relationship_score": float(_get(state, "relationship_score") or 0),
            "mood": mood_name,
            "mood_intensity": float(mood_intensity or 0),
            "tool_results": [r.output for r in (_get(state, "tool_results") or [])],
            "reject_reason": _get(state, "reject_reason"),
            "trace_id": _get(state, "trace_id"),
        }

    # ---------------- 幂等去重 ----------------

    @staticmethod
    def _idem_key(session_id: str, client_msg_id: str) -> str:
        return f"idem:{session_id}:{client_msg_id}"

    async def get_cached_result(self, session_id: str, client_msg_id: str | None) -> dict | None:
        """读取同一幂等键上次的成功结果；无则返回 None。"""
        if not client_msg_id:
            return None
        raw = await self.ctx.cache.get(self._idem_key(session_id, client_msg_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def store_result(self, session_id: str, client_msg_id: str | None, result: dict) -> None:
        """缓存本轮结果，供同幂等键的重试直接返回（不重跑图、不重复落库）。"""
        if not client_msg_id:
            return
        await self.ctx.cache.set(
            self._idem_key(session_id, client_msg_id),
            json.dumps(result, ensure_ascii=False),
            ttl=self.ctx.settings.idempotency_ttl_sec,
        )

    # ---------------- 主链路 ----------------

    async def chat(
        self, session_id: str, user_input: str, user_id: str, client_msg_id: str | None = None
    ) -> dict:
        cached = await self.get_cached_result(session_id, client_msg_id)
        if cached is not None:
            return cached
        state = await self.build_state(session_id, user_input, user_id, client_msg_id)
        try:
            result = await self.graph.ainvoke(state)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] graph invoke failed: %s", session_id, exc)
            result = state
        formatted = self.format_result(result, session_id)
        # 仅在图真正产出回复时缓存；图失败（空回复）不缓存，重试会重新执行
        if formatted.get("reply"):
            await self.store_result(session_id, client_msg_id, formatted)
        return formatted

    # ---------------- 归属校验 ----------------

    async def _load_snapshot(self, session_id: str, user_id: str, create: bool = True) -> SessionSnapshot:
        cached = await self.ctx.cache.get(f"session:{session_id}:snapshot")
        if cached:
            try:
                data = json.loads(cached)
                if data.get("user_id") != user_id:
                    raise SessionOwnershipError(f"session {session_id} 不属于用户 {user_id}")
                return SessionSnapshot(
                    session_id=session_id,
                    user_id=user_id,
                    persona=Persona.model_validate(data["persona"]),
                    relationship_stage=RelationshipStage(data["relationship_stage"]),
                    relationship_score=float(data["relationship_score"]),
                    mood=MoodState.model_validate(data["mood"]),
                )
            except SessionOwnershipError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("session cache corrupted, fallback to repo: %s", session_id)

        snap = await self.ctx.repo.get_session(session_id)
        if snap is None:
            if not create:
                raise SessionNotFoundError(f"session {session_id} 不存在")
            snap = SessionSnapshot(session_id=session_id, user_id=user_id)
            await self.ctx.repo.save_session(snap)
            return snap
        if snap.user_id != user_id:
            raise SessionOwnershipError(f"session {session_id} 不属于用户 {user_id}")
        return snap

    async def _require_owner(self, session_id: str, user_id: str) -> SessionSnapshot:
        """只读/管理类操作：会话必须已存在且属于当前用户（缺失抛 SessionNotFoundError）。"""
        return await self._load_snapshot(session_id, user_id, create=False)

    # ---------------- 记忆管理 ----------------

    async def list_memories(self, session_id: str, user_id: str):
        await self._require_owner(session_id, user_id)
        return await self.ctx.repo.list_memories(session_id)

    async def list_sessions(self, user_id: str):
        return await self.ctx.repo.list_sessions_by_user(user_id)

    async def get_snapshot(self, session_id: str, user_id: str) -> SessionSnapshot:
        return await self._require_owner(session_id, user_id)

    async def get_history(self, session_id: str, user_id: str, limit: int = 100) -> list[ChatMessage]:
        await self._require_owner(session_id, user_id)
        return await self.ctx.repo.get_recent_chat(session_id, limit)

    async def delete_memory(self, session_id: str, memory_id: str, user_id: str) -> bool:
        await self._require_owner(session_id, user_id)
        ok = await self.ctx.repo.delete_memory(session_id, memory_id)
        await self.ctx.vector_store.delete(session_id, memory_id)
        return ok

    # ---------------- 会话 ----------------

    async def reset_session(self, session_id: str, user_id: str) -> None:
        await self._require_owner(session_id, user_id)
        # 先清向量库（独立于关系库），再清关系库与缓存
        for mem in await self.ctx.vector_store.list_all(session_id):
            await self.ctx.vector_store.delete(session_id, mem.id)
        await self.ctx.repo.reset_session(session_id)
        await self.ctx.cache.delete(f"session:{session_id}:snapshot")

    async def update_persona(self, session_id: str, persona: Persona, user_id: str) -> SessionSnapshot:
        snap = await self._require_owner(session_id, user_id)
        snap.persona = persona
        await self.ctx.repo.save_session(snap)
        await self._refresh_cache(snap)
        return snap

    async def _refresh_cache(self, snap: SessionSnapshot) -> None:
        value = json.dumps(
            {
                "user_id": snap.user_id,
                "persona": snap.persona.model_dump(mode="json"),
                "relationship_stage": snap.relationship_stage.value,
                "relationship_score": snap.relationship_score,
                "mood": snap.mood.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        await self.ctx.cache.set(
            f"session:{snap.session_id}:snapshot", value, ttl=self.ctx.settings.session_ttl_sec
        )
