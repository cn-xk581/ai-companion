"""图装配：7 个节点 + 条件边 + 检查点。

```
START
  ↓
intake（人设校验 / 输入风控 / 记忆召回）
  ├─ block ─────────────────────────────────────────► persist
  ├─ review（如自伤倾向）───────────────────────────► human_gate
  ↓ soft/allow
plan（LLM：这一轮要不要用工具）
  ├─ 有工具调用 ─► execute ──────────────────────────┐
  └─ 没有 ──────────────────────────────────────────┤
                                                    ▼
respond（流式说话 + reply.polish 润色）
  ↓
safety_check（输出风控：出戏/越界/超长）
  ├─ 需人工复核 ─► human_gate（interrupt，approve/reject）
  ↓
persist（情绪/关系推进 + 记忆抽取 + 落库）→ END
```
"""
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.context import GraphContext
from app.graph.nodes.reply import respond_node, safety_check_node
from app.graph.nodes.turn import execute_node, intake_node, plan_node
from app.graph.nodes.wrapup import human_gate_node, persist_node
from app.graph.state import STATUS_AWAITING_REVIEW, STATUS_BLOCKED, STATUS_FAILED, CompanionState

INTAKE = "intake"
PLAN = "plan"
EXECUTE = "execute"
RESPOND = "respond"
SAFETY = "safety_check"
HUMAN = "human_gate"
PERSIST = "persist"

NODE_LABELS = {
    INTAKE: "读取人设与记忆",
    PLAN: "判断是否用工具",
    EXECUTE: "执行提醒/画图/表情",
    RESPOND: "组织回复",
    SAFETY: "输出安全检查",
    HUMAN: "等待人工复核",
    PERSIST: "更新关系与记忆",
}


def _route_after_intake(state: dict, ctx: GraphContext) -> str:
    if state.get("status") in (STATUS_BLOCKED, STATUS_FAILED):
        return PERSIST
    safety = state.get("safety") or {}
    if safety.get("action") == "review":
        return HUMAN
    return PLAN


def _route_after_plan(state: dict, ctx: GraphContext) -> str:
    return EXECUTE if state.get("calls") else RESPOND


def _route_after_safety(state: dict, ctx: GraphContext) -> str:
    if state.get("status") == STATUS_AWAITING_REVIEW:
        return HUMAN
    return PERSIST


def build_graph(ctx: GraphContext, checkpointer: Any = None, interrupt_after: list[str] | None = None):
    """编译图。`interrupt_after` 供运维/测试做断点续跑验证。"""
    g = StateGraph(CompanionState)

    g.add_node(INTAKE, partial(intake_node, ctx=ctx))
    g.add_node(PLAN, partial(plan_node, ctx=ctx))
    g.add_node(EXECUTE, partial(execute_node, ctx=ctx))
    g.add_node(RESPOND, partial(respond_node, ctx=ctx))
    g.add_node(SAFETY, partial(safety_check_node, ctx=ctx))
    g.add_node(HUMAN, partial(human_gate_node, ctx=ctx))
    g.add_node(PERSIST, partial(persist_node, ctx=ctx))

    g.add_edge(START, INTAKE)
    g.add_conditional_edges(
        INTAKE,
        partial(_route_after_intake, ctx=ctx),
        {PLAN: PLAN, HUMAN: HUMAN, PERSIST: PERSIST},
    )
    g.add_conditional_edges(
        PLAN, partial(_route_after_plan, ctx=ctx), {EXECUTE: EXECUTE, RESPOND: RESPOND}
    )
    g.add_edge(EXECUTE, RESPOND)
    g.add_edge(RESPOND, SAFETY)
    g.add_conditional_edges(
        SAFETY, partial(_route_after_safety, ctx=ctx), {HUMAN: HUMAN, PERSIST: PERSIST}
    )
    g.add_edge(HUMAN, PERSIST)
    g.add_edge(PERSIST, END)

    return g.compile(
        checkpointer=checkpointer, name="companion_turn", interrupt_after=interrupt_after or None
    )


def topology() -> dict[str, Any]:
    return {
        "nodes": [{"id": k, "label": v} for k, v in NODE_LABELS.items()],
        "edges": [
            [START, INTAKE],
            [INTAKE, PLAN],
            [INTAKE, HUMAN],
            [INTAKE, PERSIST],
            [PLAN, EXECUTE],
            [PLAN, RESPOND],
            [EXECUTE, RESPOND],
            [RESPOND, SAFETY],
            [SAFETY, HUMAN],
            [SAFETY, PERSIST],
            [HUMAN, PERSIST],
            [PERSIST, END],
        ],
    }
