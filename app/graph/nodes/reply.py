"""节点 4-5：respond（流式说话 + 业务润色）与 safety_check（输出风控）。"""
from __future__ import annotations

import logging

from langgraph.config import get_stream_writer

from app.agents.registry import INTAKE_AGENT, REPLIER_AGENT
from app.core.tracing import Tracer
from app.domain.companion import MOOD_LABELS, Persona
from app.graph.context import GraphContext
from app.graph.state import STATUS_AWAITING_REVIEW
from app.llm.ops import stream_failure_fallback, stream_tokens
from app.llm.prompts import build_reply_messages, build_system_prompt
from app.skills.models import SkillCall

logger = logging.getLogger(__name__)


async def respond_node(state: dict, ctx: GraphContext) -> dict:
    turn_id = state["turn_id"]
    tracer: Tracer = ctx.tracer_for(turn_id)
    updates: dict = {
        "warnings": list(state.get("warnings") or []),
        "outcomes": list(state.get("outcomes") or []),
        "skill_calls": int(state.get("skill_calls") or 0),
        "llm_calls": int(state.get("llm_calls") or 0),
    }
    # 风控短路或已有人工话术：直接用现成回复
    if state.get("reply"):
        updates["draft"] = state.get("reply")
        return updates

    persona = Persona.model_validate(state.get("persona") or {})
    stage = state.get("stage") or "stranger"
    mood = state.get("mood") or {"current_mood": "neutral", "mood_intensity": 0.5}
    safety = state.get("safety") or {}
    system = build_system_prompt(
        persona=persona,
        stage=stage,
        mood=mood.get("current_mood", "neutral"),
        intensity=float(mood.get("mood_intensity", 0.5)),
        memories=list(state.get("memories") or []),
        relation_context=state.get("relation_context") or "",
        safety_hint=(safety.get("reply_hint") or "") if safety.get("action") == "soft" else "",
        tool_context=state.get("tool_text") or "",
    )
    history = list(state.get("history") or [])[-ctx.settings.history_window :]
    messages = build_reply_messages(system=system, history=history, user_input=state["user_input"])

    writer = get_stream_writer()
    parts: list[str] = []
    try:
        async with tracer.span(
            "llm.reply",
            "llm",
            model=getattr(ctx.llm, "model", ""),
            temperature=REPLIER_AGENT.temperature,
            prompt_preview=messages[-1]["content"],
            stage=stage,
        ) as box:
            async for token in stream_tokens(
                ctx=ctx,
                messages=messages,
                temperature=REPLIER_AGENT.temperature,
                max_tokens=REPLIER_AGENT.max_output_tokens,
            ):
                parts.append(token)
                writer({"type": "token", "content": token})
            box.update({"response_preview": "".join(parts)})
        tracer.record_llm({}, model=getattr(ctx.llm, "model", ""))
        draft = "".join(parts).strip()
    except Exception as exc:  # noqa: BLE001 - 说不了话也要有话说
        logger.warning("[respond] 流式生成失败：%s", exc)
        draft = stream_failure_fallback(exc)
        updates["warnings"].append("回复生成失败，已给兜底话术")
        if not parts:
            writer({"type": "token", "content": draft})
        await tracer.decision("respond.failed", error=str(exc)[:160])

    updates["llm_calls"] += 1
    updates["draft"] = draft

    # ---- 业务润色（长度/出戏/称呼/空回复） ----
    sctx = ctx.skill_context(turn_id, state["user_id"], REPLIER_AGENT)
    polish = await ctx.executor.run(
        SkillCall(
            skill="reply.polish",
            args={
                "draft": draft,
                "stage": stage,
                "persona_name": persona.name,
                "max_chars": ctx.settings.reply_max_chars,
            },
        ),
        sctx=sctx,
    )
    updates["skill_calls"] += 1
    updates["outcomes"].append(polish.model_dump())
    if polish.ok:
        updates["reply"] = polish.data.get("text") or draft
        if polish.data.get("changes"):
            updates["warnings"].append("回复已润色：" + "；".join(polish.data["changes"]))
    else:
        updates["reply"] = draft
        updates["warnings"].append(f"回复润色失败：{polish.message}")

    await tracer.decision(
        "respond.done", length=len(updates["reply"] or ""), mood=MOOD_LABELS.get(mood.get("current_mood", ""), "")
    )
    return updates


async def safety_check_node(state: dict, ctx: GraphContext) -> dict:
    """输出侧风控：出戏、越界、超长；高风险转人工（interrupt）。"""
    turn_id = state["turn_id"]
    tracer: Tracer = ctx.tracer_for(turn_id)
    updates: dict = {
        "warnings": list(state.get("warnings") or []),
        "outcomes": list(state.get("outcomes") or []),
        "skill_calls": int(state.get("skill_calls") or 0),
    }
    reply = state.get("reply") or ""
    persona = Persona.model_validate(state.get("persona") or {})
    sctx = ctx.skill_context(turn_id, state["user_id"], INTAKE_AGENT)

    check = await ctx.executor.run(
        SkillCall(
            skill="safety.guard",
            args={
                "text": reply,
                "stage": state.get("stage") or "stranger",
                "direction": "output",
                "boundaries": persona.boundaries,
            },
        ),
        sctx=sctx,
    )
    updates["skill_calls"] += 1
    updates["outcomes"].append(check.model_dump())
    review = check.data if check.ok else {"action": "allow", "categories": []}
    updates["review"] = review

    action = review.get("action")
    if action == "review":
        updates["status"] = STATUS_AWAITING_REVIEW
        updates["warnings"].append("回复需人工复核：" + "、".join(review.get("categories") or []))
        await tracer.decision("safety.review", categories=review.get("categories"))
    elif action == "block":
        updates["reply"] = "我们换个话题吧。"
        updates["warnings"].append("回复被安全策略拦截，已替换为安全话术")
        await tracer.decision("safety.block", categories=review.get("categories"))
    elif action == "soft":
        updates["warnings"].append("回复有轻微问题：" + "、".join(review.get("categories") or []))
    return updates
