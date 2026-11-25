"""节点 1-3：intake（人设/风控/记忆）、plan（要不要用工具）、execute（调工具）。

这一层只做编排：判断走哪条路、把状态拼起来；
所有业务判断都在 Skill 里（人设校验、风控分级、关系推进……）。
"""
from __future__ import annotations

import logging

from app.agents.registry import INTAKE_AGENT, TOOLS_AGENT
from app.core.loopguard import GuardLimits, check_budget, new_guard, observe_signature
from app.core.tracing import Tracer
from app.domain.companion import STAGE_LABELS, Persona
from app.graph.context import GraphContext
from app.graph.decision import REPAIR_HINT, TurnPlan
from app.graph.state import STATUS_BLOCKED, STATUS_RUNNING
from app.llm.ops import complete_json
from app.llm.prompts import build_planner_messages
from app.skills.models import SkillCall

logger = logging.getLogger(__name__)


def guard_limits(ctx: GraphContext) -> GuardLimits:
    return GuardLimits(
        max_rounds=ctx.settings.graph_max_rounds,
        max_skill_calls=ctx.settings.graph_max_skill_calls,
        max_llm_calls=ctx.settings.graph_max_llm_calls,
        max_wall_seconds=ctx.settings.graph_max_wall_seconds,
        repeat_limit=ctx.settings.graph_repeat_limit,
        no_progress_limit=ctx.settings.graph_no_progress_limit,
    )


async def intake_node(state: dict, ctx: GraphContext) -> dict:
    turn_id = state["turn_id"]
    tracer: Tracer = ctx.tracer_for(turn_id)
    sctx = ctx.skill_context(turn_id, state["user_id"], INTAKE_AGENT)
    updates: dict = {
        "status": STATUS_RUNNING,
        "warnings": list(state.get("warnings") or []),
        "errors": list(state.get("errors") or []),
        "outcomes": list(state.get("outcomes") or []),
        "skill_calls": int(state.get("skill_calls") or 0),
        "llm_calls": int(state.get("llm_calls") or 0),
        "guard": state.get("guard") or new_guard(),
    }

    # ---- 人设归一化（脏人设不能进提示词） ----
    persona_out = await ctx.executor.run(
        SkillCall(skill="persona.normalize", args={"persona": state.get("persona") or {}}),
        sctx=sctx,
    )
    updates["skill_calls"] += 1
    updates["outcomes"].append(persona_out.model_dump())
    if not persona_out.ok:
        updates["status"] = "failed"
        updates["errors"].append(
            {"node": "intake", "kind": persona_out.error_kind, "message": persona_out.message}
        )
        await tracer.decision("intake.persona_failed", message=persona_out.message)
        return updates
    persona = Persona.model_validate(persona_out.data["persona"])
    updates["persona"] = persona.model_dump()

    # ---- 输入风控 ----
    guard_out = await ctx.executor.run(
        SkillCall(
            skill="safety.guard",
            args={
                "text": state["user_input"],
                "stage": state.get("stage") or "stranger",
                "direction": "input",
                "boundaries": persona.boundaries,
            },
        ),
        sctx=sctx,
    )
    updates["skill_calls"] += 1
    updates["outcomes"].append(guard_out.model_dump())
    safety = guard_out.data if guard_out.ok else {"action": "allow", "categories": []}
    updates["safety"] = safety

    if safety.get("action") == "block":
        # 明确违规：不调 LLM，直接给安全话术
        updates["status"] = STATUS_BLOCKED
        updates["reply"] = safety.get("reply_hint") or "这个我们换个话题吧。"
        updates["warnings"].append(f"输入被拦截：{'、'.join(safety.get('categories') or [])}")
        await tracer.decision("intake.blocked", categories=safety.get("categories"))
        return updates

    if safety.get("action") == "review":
        # 高风险（如自伤倾向）：先给关怀话术，同时转人工复核
        updates["reply"] = safety.get("reply_hint") or ""
        updates["warnings"].append(f"输入需人工复核：{'、'.join(safety.get('categories') or [])}")
        await tracer.decision("intake.needs_review", categories=safety.get("categories"))
        return updates

    # ---- 长期记忆召回（失败不阻断） ----
    query = _recall_query(state["user_input"], state.get("stage") or "stranger", state.get("mood") or {})
    recall = await ctx.executor.run(
        SkillCall(skill="memory.recall", args={"query": query, "top_k": ctx.settings.memory_top_k}),
        sctx=sctx,
    )
    updates["skill_calls"] += 1
    updates["outcomes"].append(recall.model_dump())
    updates["memories"] = list(recall.data.get("memories") or []) if recall.ok else []
    if not recall.ok:
        updates["warnings"].append("长期记忆召回失败，本轮不使用历史记忆")

    # ---- 预算闸门 ----
    guard, reason = check_budget(
        updates["guard"],
        guard_limits(ctx),
        round_no=1,
        skill_calls=updates["skill_calls"],
        llm_calls=updates["llm_calls"],
    )
    updates["guard"] = guard
    if reason:
        updates["warnings"].append(f"触发预算护栏：{reason}")
        updates["status"] = STATUS_BLOCKED
        updates["reply"] = "这一轮我先缓一下，我们等下再聊。"
        return updates

    await tracer.decision(
        "intake.done",
        stage=state.get("stage"),
        memories=len(updates["memories"]),
        safety=safety.get("action"),
    )
    return updates


async def plan_node(state: dict, ctx: GraphContext) -> dict:
    """决定这一轮是否需要工具。绝大多数对话不需要，直接返回空调用。"""
    turn_id = state["turn_id"]
    tracer: Tracer = ctx.tracer_for(turn_id)
    updates: dict = {"calls": [], "warnings": list(state.get("warnings") or [])}

    if state.get("status") == STATUS_BLOCKED or state.get("reply"):
        # 已被风控短路，或已有人工/兜底回复：不规划工具
        return updates

    visible = [
        m for m in await ctx.skills.list_public()
        if m["enabled"] and TOOLS_AGENT.can_use_skill(m["name"])
    ]
    if not visible:
        return updates

    persona = Persona.model_validate(state.get("persona") or {})
    messages = build_planner_messages(
        persona=persona,
        stage=state.get("stage") or "stranger",
        user_input=state["user_input"],
        tools=visible,
        recent=list(state.get("history") or [])[-2:],
    )
    try:
        result = await complete_json(
            ctx=ctx,
            tracer=tracer,
            messages=messages,
            temperature=0.1,
            max_tokens=300,
            purpose="plan",
        )
        plan = TurnPlan.model_validate(result.data)
    except Exception as exc:  # noqa: BLE001 - 规划失败=按纯聊天处理，不阻断
        await tracer.decision("plan.failed", message=str(exc)[:160])
        updates["warnings"].append("工具规划失败，本轮按纯聊天回复")
        return updates

    calls = [c for c in plan.tool_calls if TOOLS_AGENT.can_use_skill(c.skill)]
    dropped = [c.skill for c in plan.tool_calls if not TOOLS_AGENT.can_use_skill(c.skill)]
    if dropped:
        updates["warnings"].append("丢弃了未授权的工具调用：" + "、".join(dropped))
        await tracer.decision("plan.dropped_tools", dropped=dropped)

    guard, trip = observe_signature(
        state.get("guard") or {},
        guard_limits(ctx),
        [c.model_dump() for c in calls],
    )
    updates["guard"] = guard
    updates["llm_calls"] = int(state.get("llm_calls") or 0) + 1
    updates["plan"] = plan.model_dump()
    updates["calls"] = [c.model_dump() for c in calls]
    if trip:
        updates["warnings"].append(f"检测到重复工具调用：{trip}")
        updates["calls"] = []

    await tracer.decision(
        "plan.done", tools=[c.skill for c in calls], tone_hint=plan.tone_hint, thoughts=plan.thoughts
    )
    return updates


async def execute_node(state: dict, ctx: GraphContext) -> dict:
    turn_id = state["turn_id"]
    tracer: Tracer = ctx.tracer_for(turn_id)
    calls = [SkillCall.model_validate(c) for c in (state.get("calls") or [])]
    updates: dict = {
        "warnings": list(state.get("warnings") or []),
        "outcomes": list(state.get("outcomes") or []),
        "skill_calls": int(state.get("skill_calls") or 0),
    }
    if not calls:
        updates["tool_text"] = ""
        return updates

    sctx = ctx.skill_context(turn_id, state["user_id"], TOOLS_AGENT)
    calls = [_hydrate(call, state) for call in calls]
    outcomes = await ctx.executor.run_many(calls, sctx=sctx, max_calls=ctx.settings.max_skill_calls_per_round)
    updates["skill_calls"] += len(outcomes)
    updates["outcomes"].extend([o.model_dump() for o in outcomes])

    lines: list[str] = []
    for outcome in outcomes:
        if not outcome.ok:
            lines.append(f"- {outcome.skill} 没做成：{outcome.user_message or outcome.message}")
            continue
        data = outcome.data
        if outcome.skill == "image.reply":
            lines.append(f"- 画了一张图：{data.get('url')}（配文：{data.get('caption')}）")
        elif outcome.skill == "reminder.create":
            lines.append(f"- 已记下提醒：{data.get('message')}")
        elif outcome.skill == "meme.reply":
            lines.append(f"- 挑了个表情：{data.get('emoji')} {data.get('caption')}")
        else:
            lines.append(f"- {outcome.skill} 已完成")
    updates["tool_text"] = "\n".join(lines)
    await tracer.decision(
        "execute.done",
        tools=[o.skill for o in outcomes],
        ok=[o.ok for o in outcomes],
    )
    return updates


def _recall_query(user_input: str, stage: str, mood: dict) -> str:
    label = STAGE_LABELS.get(stage, stage)
    current = (mood or {}).get("current_mood", "neutral")
    return f"{label}阶段，此刻情绪{current}；用户说：{user_input[:120]}"


def _hydrate(call: SkillCall, state: dict) -> SkillCall:
    """把编排上下文补进参数（用户身份等），模型不必也不能伪造这些字段。"""
    args = dict(call.args or {})
    if call.skill == "reminder.create":
        args["user_id"] = state["user_id"]
    return SkillCall(skill=call.skill, args=args)
