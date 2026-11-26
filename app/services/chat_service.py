"""对话调度层：一轮对话 = 一个 LangGraph thread（可中断、可恢复、可审计）。

职责
----
- 组装 state（会话快照 + 最近历史 + 输入），调图，落轮次记录；
- 会话 CRUD、人设修改、记忆管理（全部做归属校验）；
- SSE 流式：图里用 `get_stream_writer` 推 token，这里转成 SSE 帧；
- 人工复核：图停在 `interrupt`，这里把暂停信息返回前端，`resume` 用 `Command(resume=…)` 继续。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from langgraph.types import Command

from app.core.checkpointing import thread_config
from app.core.loopguard import new_guard
from app.domain.companion import ChatMessage, MoodState, Persona, RelationshipStage, TurnStatus
from app.graph.context import GraphContext
from app.graph.nodes.wrapup import SAFE_REPLY
from app.storage.postgres import SessionRecord

logger = logging.getLogger(__name__)


class SessionOwnershipError(Exception):
    """会话不属于当前用户。"""


class SessionNotFoundError(Exception):
    """会话不存在。"""


class TurnNotFoundError(Exception):
    """轮次不存在。"""


class ChatService:
    def __init__(self, ctx: GraphContext, graph, checkpointer_desc: dict[str, Any]) -> None:
        self.ctx = ctx
        self.graph = graph
        self.checkpointer_desc = checkpointer_desc

    # ---------------- 会话 ----------------

    async def create_session(self, session_id: str | None, user_id: str, greeting: str = "") -> SessionRecord:
        session_id = session_id or uuid.uuid4().hex
        existing = await self.ctx.repo.get_session(session_id)
        if existing is not None:
            if existing.user_id != user_id:
                raise SessionOwnershipError(f"会话 {session_id} 不属于当前用户")
            return existing
        record = SessionRecord(session_id=session_id, user_id=user_id)
        await self.ctx.repo.save_session(record)
        if greeting:
            await self.ctx.repo.append_message(
                session_id, ChatMessage(role="assistant", content=greeting)
            )
        return record

    async def require_session(self, session_id: str, user_id: str) -> SessionRecord:
        record = await self.ctx.repo.get_session(session_id)
        if record is None:
            raise SessionNotFoundError(f"会话不存在：{session_id}")
        if record.user_id != user_id:
            raise SessionOwnershipError(f"会话 {session_id} 不属于当前用户")
        return record

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        records = await self.ctx.repo.list_sessions_by_user(user_id)
        items: list[dict[str, Any]] = []
        for record in records:
            recent = await self.ctx.repo.recent_messages(record.session_id, 1)
            last = recent[-1] if recent else None
            items.append(
                {
                    "session_id": record.session_id,
                    "name": record.persona.name,
                    "avatar": record.persona.avatar,
                    "stage": record.stage.value,
                    "stage_label": _stage_label(record.stage),
                    "score": round(record.score, 1),
                    "mood": record.mood.current_mood,
                    "mood_label": _mood_label(record.mood),
                    "last_message": (last.content[:60] if last else ""),
                    "last_time": (last.timestamp if last else record.updated_at),
                }
            )
        return items

    async def update_persona(self, session_id: str, persona: Persona, user_id: str) -> SessionRecord:
        record = await self.require_session(session_id, user_id)
        record.persona = persona
        await self.ctx.repo.save_session(record)
        await self.ctx.cache.delete(f"session:{session_id}:snapshot")
        return record

    async def reset_session(
        self,
        session_id: str,
        user_id: str,
        *,
        clear_memories: bool = True,
        mode: str | None = None,
    ) -> dict[str, Any]:
        record = await self.require_session(session_id, user_id)
        # keep：只清对话；session：清对话 + 这段会话的情景/摘要；all：连羁绊档案一起清
        resolved = mode or ("session" if clear_memories else "keep")
        await self.ctx.repo.clear_messages(session_id)
        record.stage = RelationshipStage.STRANGER
        record.score = 0.0
        record.mood = MoodState()
        await self.ctx.repo.save_session(record)
        await self.ctx.cache.delete(f"session:{session_id}:snapshot")

        memory = getattr(self.ctx, "memory", None)
        relations = getattr(self.ctx, "relations", None)
        if memory is not None and relations is not None and record.character_id:
            relation = await memory.relation_for_session(user_id, session_id)
            if relation is not None:
                if resolved == "session":
                    await memory.clear_session_memory(relation.id, session_id)
                elif resolved == "all":
                    await memory.clear_relation_memory(relation.id)
        elif resolved == "all":
            memories = await self.ctx.repo.list_memories(user_id)
            for memory in memories:
                if self.ctx.vector_store is not None:
                    await self.ctx.vector_store.delete(user_id, memory.id)
            await self.ctx.repo.clear_memories(user_id)
        return {"session_id": session_id, "cleared_memories": resolved != "keep", "mode": resolved}

    async def list_memories(self, user_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id:
            await self.require_session(session_id, user_id)
        rows = await self.ctx.repo.list_memories(user_id)
        return [
            {
                "id": m.id,
                "content": m.content,
                "importance": round(m.importance, 2),
                "category": m.category,
                "tags": m.tags,
                "created_at": m.created_at,
            }
            for m in rows
        ]

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        ok = await self.ctx.repo.delete_memory(user_id, memory_id)
        if ok and self.ctx.vector_store is not None:
            await self.ctx.vector_store.delete(user_id, memory_id)
        return ok

    # ---------------- 一轮对话 ----------------

    async def chat(
        self,
        *,
        session_id: str,
        user_id: str,
        user_input: str,
        client_msg_id: str | None = None,
    ) -> dict[str, Any]:
        cached = await self._cached(session_id, client_msg_id)
        if cached is not None:
            return cached

        state = await self._build_state(session_id, user_id, user_input, client_msg_id)
        payload = await self._invoke(state, session_id, user_id)
        if payload.get("reply"):
            await self._store_cache(session_id, client_msg_id, payload)
        return payload

    async def stream(
        self,
        *,
        session_id: str,
        user_id: str,
        user_input: str,
        client_msg_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """SSE 事件流：meta → token* → (done | error)。"""
        cached = await self._cached(session_id, client_msg_id)
        if cached is not None:
            yield {"type": "done", **cached}
            return

        state = await self._build_state(session_id, user_id, user_input, client_msg_id)
        yield {"type": "meta", "turn_id": state["turn_id"], "session_id": session_id}

        final: dict[str, Any] = dict(state)
        config = thread_config(state["turn_id"], user_id)
        self.ctx.start_tracer(state["turn_id"])
        async with self.ctx.cache.lock(
            f"lock:session:{session_id}",
            timeout=self.ctx.settings.lock_acquire_timeout_sec,
            ttl=self.ctx.settings.lock_ttl_sec,
        ) as acquired:
            if not acquired:
                yield {"type": "error", "message": "上一条消息还在处理中，稍等一下"}
                return
            try:
                async for mode, chunk in self.graph.astream(
                    state, config=config, stream_mode=["custom", "values"]
                ):
                    if mode == "custom":
                        for item in _iter_custom(chunk):
                            if isinstance(item, dict) and item.get("type") == "token":
                                yield {"type": "token", "content": item.get("content", "")}
                    elif mode == "values":
                        final = chunk
            except Exception as exc:  # noqa: BLE001
                logger.exception("[chat] 流式执行失败 turn=%s", state["turn_id"])
                await self.ctx.repo.save_turn(
                    state["turn_id"], session_id, user_id, TurnStatus.FAILED, {"error": str(exc)[:400]}
                )
                yield {"type": "error", "message": "这次没接住，稍后再试一次好吗？"}
                return

        payload = await self._finalize(state["turn_id"], session_id, user_id, final)
        if payload.get("reply"):
            await self._store_cache(session_id, client_msg_id, payload)
        yield {"type": "done", **payload}

    async def resume(
        self,
        *,
        turn_id: str,
        user_id: str,
        decision: str,
        feedback: str = "",
        by: str = "operator",
    ) -> dict[str, Any]:
        record = await self._require_turn(turn_id, user_id)
        session_id = str(record["session_id"])
        self.ctx.start_tracer(turn_id)
        final = await self.graph.ainvoke(
            Command(resume={"decision": decision, "feedback": feedback, "by": by}),
            config=thread_config(turn_id, user_id),
        )
        return await self._finalize(turn_id, session_id, user_id, final)

    async def get_turn(self, turn_id: str, user_id: str) -> dict[str, Any]:
        record = await self._require_turn(turn_id, user_id)
        payload = record.get("payload") or {}
        return {
            "turn_id": record["turn_id"],
            "session_id": record["session_id"],
            "status": record["status"],
            "result": payload.get("result"),
            "interrupt": payload.get("interrupt"),
            "warnings": payload.get("warnings") or [],
        }

    async def trace(self, turn_id: str, user_id: str, limit: int = 500) -> dict[str, Any]:
        await self._require_turn(turn_id, user_id)
        events = await self.ctx.repo.list_events(turn_id, limit=limit)
        return {"turn_id": turn_id, "count": len(events), "events": events}

    # ---------------- 内部 ----------------

    async def _build_state(
        self, session_id: str, user_id: str, user_input: str, client_msg_id: str | None
    ) -> dict[str, Any]:
        record = await self.require_session(session_id, user_id)
        history = await self.ctx.repo.recent_messages(session_id, self.ctx.settings.history_window)
        return {
            "turn_id": uuid.uuid4().hex,
            "session_id": session_id,
            "user_id": user_id,
            "user_input": user_input,
            "client_msg_id": client_msg_id,
            "persona": record.persona.model_dump(mode="json"),
            "stage": record.stage.value,
            "score": record.score,
            "mood": record.mood.model_dump(mode="json"),
            "history": [{"role": m.role, "content": m.content} for m in history],
            "status": TurnStatus.RUNNING,
            "guard": new_guard(),
            "warnings": [],
            "errors": [],
            "outcomes": [],
            "calls": [],
            "llm_calls": 0,
            "skill_calls": 0,
            "started_at": time.time(),
        }

    async def _invoke(self, state: dict[str, Any], session_id: str, user_id: str) -> dict[str, Any]:
        turn_id = state["turn_id"]
        self.ctx.start_tracer(turn_id)
        async with self.ctx.cache.lock(
            f"lock:session:{session_id}",
            timeout=self.ctx.settings.lock_acquire_timeout_sec,
            ttl=self.ctx.settings.lock_ttl_sec,
        ) as acquired:
            if not acquired:
                return {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "status": TurnStatus.RUNNING,
                    "reply": "",
                    "warning": "上一条消息还在处理中，请稍后",
                }
            try:
                final = await self.graph.ainvoke(state, config=thread_config(turn_id, user_id))
            except Exception as exc:  # noqa: BLE001 - 图崩溃要留记录，不吞
                logger.exception("[chat] 执行失败 turn=%s", turn_id)
                await self.ctx.repo.save_turn(
                    turn_id, session_id, user_id, TurnStatus.FAILED, {"error": str(exc)[:400]}
                )
                await self.ctx.repo.append_event(turn_id, {"type": "graph.crashed", "error": str(exc)[:400]})
                return {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "status": TurnStatus.FAILED,
                    "reply": "",
                    "error": str(exc)[:400],
                }
        return await self._finalize(turn_id, session_id, user_id, final)

    async def _finalize(
        self, turn_id: str, session_id: str, user_id: str, final: dict[str, Any]
    ) -> dict[str, Any]:
        interrupt = _extract_interrupt(final)
        result = (final or {}).get("result")
        status = str((final or {}).get("status") or TurnStatus.RUNNING)
        if interrupt is not None:
            status = TurnStatus.AWAITING_REVIEW
        payload = {
            "turn_id": turn_id,
            "session_id": session_id,
            "status": status,
            "reply": (result or {}).get("reply") or (final or {}).get("reply") or "",
            "stage": (result or {}).get("stage") or (final or {}).get("stage"),
            "stage_label": (result or {}).get("stage_label"),
            "score": (result or {}).get("score"),
            "mood": (result or {}).get("mood"),
            "mood_label": (result or {}).get("mood_label"),
            "result": result,
            "interrupt": interrupt,
            "warnings": list((final or {}).get("warnings") or []),
        }
        await self.ctx.repo.save_turn(
            turn_id,
            session_id,
            user_id,
            status,
            {
                "result": result,
                "interrupt": interrupt,
                "warnings": payload["warnings"],
                "trace_summary": (final or {}).get("trace_summary"),
            },
        )
        return payload

    async def _require_turn(self, turn_id: str, user_id: str) -> dict[str, Any]:
        record = await self.ctx.repo.get_turn(turn_id)
        if record is None:
            raise TurnNotFoundError(f"轮次不存在：{turn_id}")
        if record.get("user_id") != user_id:
            raise SessionOwnershipError(f"轮次 {turn_id} 不属于当前用户")
        return record

    def _idem_key(self, session_id: str, client_msg_id: str | None) -> str:
        return f"idem:chat:{session_id}:{client_msg_id}" if client_msg_id else ""

    async def _cached(self, session_id: str, client_msg_id: str | None) -> dict[str, Any] | None:
        key = self._idem_key(session_id, client_msg_id)
        if not key:
            return None
        raw = await self.ctx.cache.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def _store_cache(self, session_id: str, client_msg_id: str | None, payload: dict[str, Any]) -> None:
        key = self._idem_key(session_id, client_msg_id)
        if not key:
            return
        try:
            await self.ctx.cache.set(
                key, json.dumps(payload, ensure_ascii=False, default=str),
                ttl=self.ctx.settings.idempotency_ttl_sec,
            )
        except Exception:  # noqa: BLE001
            return


def _iter_custom(chunk: Any):
    if isinstance(chunk, (list, tuple)):
        return chunk
    return [chunk]


def _extract_interrupt(final: dict[str, Any]) -> Any:
    raw = (final or {}).get("__interrupt__")
    if not raw:
        return None
    try:
        first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        return getattr(first, "value", first)
    except Exception:  # noqa: BLE001
        return None


def _stage_label(stage: RelationshipStage) -> str:
    from app.domain.companion import STAGE_LABELS

    return STAGE_LABELS.get(stage.value, stage.value)


def _mood_label(mood: MoodState) -> str:
    from app.domain.companion import MOOD_LABELS

    return MOOD_LABELS.get(mood.current_mood, mood.current_mood)


__all__ = [
    "ChatService",
    "SessionOwnershipError",
    "SessionNotFoundError",
    "TurnNotFoundError",
    "SAFE_REPLY",
]
