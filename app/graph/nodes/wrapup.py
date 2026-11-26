"""节点 6-7：human_gate（人工复核）与 persist（落库 + 关系/情绪/记忆）。

人工复核只用于「输出侧高风险」：把草稿冻结在检查点，运营 approve 后照常发出，
reject 则替换为安全话术。审批动作与理由都会写进审计事件。
"""
from __future__ import annotations

import json
import logging

from langgraph.types import interrupt

from app.agents.registry import STATE_AGENT
from app.core.tracing import Tracer
from app.domain.companion import (
    MOOD_LABELS,
    STAGE_LABELS,
    ChatMessage,
    MoodState,
    Persona,
    RelationshipStage,
)
from app.graph.context import GraphContext
from app.graph.state import STATUS_AWAITING_REVIEW, STATUS_BLOCKED, STATUS_DONE
from app.llm.ops import complete_json
from app.llm.prompts import build_extract_messages
from app.skills.models import SkillCall

logger = logging.getLogger(__name__)

SAFE_REPLY = "那我们换个话题吧。"


async def human_gate_node(state: dict, ctx: GraphContext) -> dict:
    turn_id = state["turn_id"]
    tracer = ctx.tracer_for(turn_id)
    # 两个来源都可能触发复核：输入侧（如自伤）与输出侧（越界草稿）
    review = state.get("review") or {}
    safety = state.get("safety") or {}
    source = review if review.get("action") == "review" else safety
    payload = {
        "type": "reply_review",
        "turn_id": turn_id,
        "session_id": state.get("session_id"),
        "stage": state.get("stage"),
        "reason": source.get("reason") or "回复需要人工确认",
        "categories": source.get("categories") or [],
        "draft": (state.get("reply") or "")[:600],
        "options": ["approve", "reject"],
    }
    await tracer.decision("human.requested", categories=payload["categories"], reason=payload["reason"])

    raw = interrupt(payload)
    if isinstance(raw, dict):
        decision = str(raw.get("decision") or "reject").lower()
        feedback = str(raw.get("feedback") or "")[:300]
        by = str(raw.get("by") or "operator")
    else:
        decision, feedback, by = str(raw or "reject").lower(), "", "operator"
    if decision not in ("approve", "reject"):
        decision = "reject"

    updates: dict = {
        "human": {"decision": decision, "feedback": feedback, "by": by},
        "warnings": list(state.get("warnings") or []),
    }
    if decision == "reject":
        updates["reply"] = SAFE_REPLY
        updates["warnings"].append("人工驳回，已替换为安全话术")
    else:
        updates["warnings"].append("人工审核通过")
    updates["status"] = STATUS_DONE
    await tracer.decision("human.decided", decision=decision, by=by, feedback=feedback)
    return updates


async def persist_node(state: dict, ctx: GraphContext) -> dict:
    turn_id = state["turn_id"]
    session_id = state["session_id"]
    user_id = state["user_id"]
    tracer: Tracer = ctx.tracer_for(turn_id)
    warnings = list(state.get("warnings") or [])
    outcomes = list(state.get("outcomes") or [])
    skill_calls = int(state.get("skill_calls") or 0)
    llm_calls = int(state.get("llm_calls") or 0)
    sctx = ctx.skill_context(turn_id, user_id, STATE_AGENT)

    persona = Persona.model_validate(state.get("persona") or {})
    reply = state.get("reply") or ""
    safety = state.get("safety") or {}
    status = STATUS_BLOCKED if safety.get("action") == "block" else STATUS_DONE

    # ---- 1) 情绪推进（决定关系信号） ----
    mood_old = MoodState.model_validate(state.get("mood") or {})
    mood_out = await ctx.executor.run(
        SkillCall(skill="mood.update", args={"text": state["user_input"], "mood": mood_old.model_dump()}),
        sctx=sctx,
    )
    skill_calls += 1
    outcomes.append(mood_out.model_dump())
    new_mood = MoodState.model_validate(mood_out.data["mood"]) if mood_out.ok else mood_old
    delta = float(mood_out.data.get("delta", 0.0)) if mood_out.ok else 0.0
    sentiment = "positive" if delta > 0.05 else "negative" if delta < -0.05 else "neutral"

    # ---- 2) 关系推进 ----
    rel_out = await ctx.executor.run(
        SkillCall(
            skill="relationship.update",
            args={
                "score": float(state.get("score") or 0.0),
                "stage": state.get("stage") or "stranger",
                "signals": {
                    "sentiment": sentiment,
                    "safety_action": safety.get("action") or "allow",
                    "used_tool": bool(state.get("calls")),
                },
            },
        ),
        sctx=sctx,
    )
    skill_calls += 1
    outcomes.append(rel_out.model_dump())
    if rel_out.ok:
        new_score = float(rel_out.data["score"])
        new_stage = RelationshipStage(rel_out.data["stage"])
        rel_delta = float(rel_out.data.get("delta", 0.0))
        if rel_out.data.get("stage_changed"):
            warnings.append(f"关系阶段变化：{rel_out.data.get('stage_label')}")
    else:
        new_score = float(state.get("score") or 0.0)
        new_stage = RelationshipStage(state.get("stage") or "stranger")
        rel_delta = 0.0
        warnings.append(f"关系推进失败：{rel_out.message}")

    # ---- 3) 记忆抽取（只对正常完成的对话，失败不阻断） ----
    if ctx.settings.memory_extract_enabled and status == STATUS_DONE and safety.get("action") == "allow" and reply:
        try:
            extraction = await complete_json(
                ctx=ctx,
                tracer=tracer,
                messages=build_extract_messages(persona=persona, user_input=state["user_input"], reply=reply),
                temperature=0.0,
                max_tokens=300,
                purpose="extract",
            )
            llm_calls += 1
            facts, episodes = _memory_items(extraction.data, ctx.settings.memory_extract_max)
            if (facts or episodes) and getattr(ctx, "memory", None) is not None:
                relation = await ctx.memory.relation_for_session(
                    state["user_id"], state["session_id"]
                )
                if relation is not None:
                    await ctx.memory.remember(
                        relation.id,
                        state["session_id"],
                        facts=facts,
                        episodes=episodes,
                    )
        except Exception as exc:  # noqa: BLE001 - 记忆失败不影响这轮对话
            logger.warning("[persist] 记忆抽取失败：%s", exc)
            warnings.append("这轮没能记住新的事（不影响回复）")

    # ---- 4) 落库：消息 + 会话快照 ----
    try:
        await ctx.repo.append_message(session_id, ChatMessage(role="user", content=state["user_input"]))
        if reply:
            await ctx.repo.append_message(session_id, ChatMessage(role="assistant", content=reply))
        from app.storage.postgres import SessionRecord

        existing = await ctx.repo.get_session(session_id)
        await ctx.repo.save_session(
            SessionRecord(
                session_id=session_id,
                user_id=user_id,
                persona=persona,
                stage=new_stage,
                score=round(new_score, 2),
                mood=new_mood,
                character_id=existing.character_id if existing else None,
                character_version_id=existing.character_version_id if existing else None,
            )
        )
        if getattr(ctx, "memory", None) is not None and getattr(ctx, "relations", None) is not None:
            try:
                await ctx.memory.sync_relation_state(
                    user_id, session_id, new_score, new_stage.value, new_mood.current_mood
                )
                await ctx.memory.maybe_summarize(user_id, session_id)
            except Exception as exc:  # noqa: BLE001 - 记忆同步失败不阻断
                logger.warning("[memory] 关系同步/摘要失败：%s", exc)
        snapshot = json.dumps(
            {
                "user_id": user_id,
                "persona": persona.model_dump(mode="json"),
                "stage": new_stage.value,
                "score": round(new_score, 2),
                "mood": new_mood.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        await ctx.cache.set(
            f"session:{session_id}:snapshot", snapshot, ttl=ctx.settings.session_ttl_sec
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[persist] 落库失败 turn=%s", turn_id)
        warnings.append(f"会话保存失败：{exc}")

    # ---- 5) 标准输出 ----
    summary = tracer.summary_dict()
    summary["llm_calls"] = max(int(summary.get("llm_calls", 0)), llm_calls)
    summary["skill_calls"] = max(int(summary.get("skill_calls", 0)), skill_calls)
    result = {
        "turn_id": turn_id,
        "session_id": session_id,
        "reply": reply,
        "status": status,
        "stage": new_stage.value,
        "stage_label": STAGE_LABELS[new_stage.value],
        "score": round(new_score, 2),
        "score_delta": rel_delta,
        "mood": new_mood.current_mood,
        "mood_label": MOOD_LABELS.get(new_mood.current_mood, new_mood.current_mood),
        "mood_intensity": new_mood.mood_intensity,
        "memories_used": list(state.get("memories") or []),
        "tools": [
            {"skill": o.get("skill"), "ok": o.get("ok")}
            for o in outcomes
            if o.get("skill") in ("reminder.create", "image.reply", "meme.reply")
        ],
        "rejected": safety.get("action") if safety.get("action") in ("block", "review") else None,
        "review": state.get("review"),
        "human": state.get("human"),
        "warnings": warnings,
        "errors": list(state.get("errors") or []),
        "trace_summary": summary,
    }
    await tracer.decision("turn.persisted", status=status, stage=new_stage.value, score=result["score"])
    ctx.drop_tracer(turn_id)
    return {
        "result": result,
        "status": status,
        "score": result["score"],
        "stage": new_stage.value,
        "mood": new_mood.model_dump(),
        "trace_summary": summary,
        "warnings": warnings,
        "skill_calls": skill_calls,
        "llm_calls": llm_calls,
        "outcomes": outcomes,
    }


def _memory_items(data: dict, limit: int) -> tuple[list[dict], list[dict]]:
    facts: list[dict] = []
    for item in (data or {}).get("facts") or []:
        value = str(item.get("value") or "").strip()
        if len(value) < 2:
            continue
        facts.append(
            {
                "category": str(item.get("category") or "profile"),
                "key": str(item.get("key") or value)[:64],
                "value": value[:255],
                "confidence": float(item.get("confidence", 0.85)),
            }
        )
        if len(facts) >= limit:
            break
    episodes: list[dict] = []
    for item in (data or {}).get("events") or []:
        summary = str(item.get("summary") or "").strip()
        if len(summary) < 2:
            continue
        episodes.append(
            {
                "kind": "event",
                "summary": summary[:300],
                "emotion_tag": str(item.get("emotion") or "")[:16],
                "importance": max(1, min(10, int(item.get("importance", 3)))),
            }
        )
        if len(episodes) >= limit:
            break
    # 兼容旧格式 {"memories":[...]}
    for item in (data or {}).get("memories") or []:
        content = str(item.get("content") or "").strip()
        if len(content) < 2:
            continue
        category = str(item.get("category") or "fact")
        if category in ("preference", "fact", "profile", "taboo", "social"):
            facts.append(
                {
                    "category": category if category != "fact" else "profile",
                    "key": content[:64],
                    "value": content,
                    "confidence": 0.7,
                }
            )
        else:
            episodes.append(
                {
                    "kind": "event",
                    "summary": content,
                    "importance": max(1, min(10, round(float(item.get("importance", 0.5)) * 10) or 3)),
                }
            )
    return facts[:limit], episodes[:limit]


def _legacy_memory_items(data: dict, limit: int) -> list[dict]:
    """校验并裁剪抽取结果：字段不合法/过短/重复的丢弃。"""
    raw = (data or {}).get("memories")
    if not isinstance(raw, list):
        return []
    allowed = {"fact", "preference", "event", "anniversary", "story"}
    items: list[dict] = []
    seen: set[str] = set()
    for item in raw[: max(1, limit)]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        category = str(item.get("category") or "fact")
        if len(content) < 3 or len(content) > 60 or content in seen:
            continue
        if category not in allowed:
            category = "fact"
        try:
            importance = float(item.get("importance", 0.6))
        except (TypeError, ValueError):
            importance = 0.6
        importance = max(0.3, min(1.0, importance))
        seen.add(content)
        items.append({"content": content, "importance": importance, "category": category})
    return items


__all__ = ["human_gate_node", "persist_node", "SAFE_REPLY", "STATUS_AWAITING_REVIEW"]
