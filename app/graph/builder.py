"""LangGraph 图装配：八节点 + 条件边。

数据流：
START -> memory_retrieve -> state_guard
  state_guard --reject--> output_safety        （风控：不再走 LLM）
  state_guard --ok------> agent_planner
    agent_planner --tool--> tool_exec -> generate_reply
    agent_planner --reply-> generate_reply
  generate_reply -> memory_extract -> output_safety -> persist -> END
"""
from __future__ import annotations

import logging
from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph.context import GraphContext
from app.graph.state import AgentState
from app.graph.nodes import (
    agent_planner,
    generate_reply,
    memory_extract,
    memory_retrieve,
    output_safety,
    persist,
    state_guard,
    tool_exec,
)

logger = logging.getLogger(__name__)

# 节点名常量
MEMORY_RETRIEVE = "memory_retrieve"
STATE_GUARD = "state_guard"
AGENT_PLANNER = "agent_planner"
TOOL_EXEC = "tool_exec"
GENERATE_REPLY = "generate_reply"
MEMORY_EXTRACT = "memory_extract"
OUTPUT_SAFETY = "output_safety"
PERSIST = "persist"


def _route_after_guard(state: AgentState) -> str:
    return OUTPUT_SAFETY if state.reject_reason else AGENT_PLANNER


def _route_after_planner(state: AgentState) -> str:
    return TOOL_EXEC if state.need_tool_call else GENERATE_REPLY


def build_graph(ctx: GraphContext):
    """构建并编译 LangGraph。ctx 通过闭包注入各节点。"""
    g = StateGraph(AgentState)

    g.add_node(MEMORY_RETRIEVE, partial(memory_retrieve.memory_retrieve_node, ctx=ctx))
    g.add_node(STATE_GUARD, partial(state_guard.state_guard_node, ctx=ctx))
    g.add_node(AGENT_PLANNER, partial(agent_planner.agent_planner_node, ctx=ctx))
    g.add_node(TOOL_EXEC, partial(tool_exec.tool_exec_node, ctx=ctx))
    g.add_node(GENERATE_REPLY, partial(generate_reply.generate_reply_node, ctx=ctx))
    g.add_node(MEMORY_EXTRACT, partial(memory_extract.memory_extract_node, ctx=ctx))
    g.add_node(OUTPUT_SAFETY, partial(output_safety.output_safety_node, ctx=ctx))
    g.add_node(PERSIST, partial(persist.persist_node, ctx=ctx))

    g.add_edge(START, MEMORY_RETRIEVE)
    g.add_edge(MEMORY_RETRIEVE, STATE_GUARD)
    g.add_conditional_edges(
        STATE_GUARD,
        _route_after_guard,
        {OUTPUT_SAFETY: OUTPUT_SAFETY, AGENT_PLANNER: AGENT_PLANNER},
    )
    g.add_conditional_edges(
        AGENT_PLANNER,
        _route_after_planner,
        {TOOL_EXEC: TOOL_EXEC, GENERATE_REPLY: GENERATE_REPLY},
    )
    g.add_edge(TOOL_EXEC, GENERATE_REPLY)
    g.add_edge(GENERATE_REPLY, MEMORY_EXTRACT)
    g.add_edge(MEMORY_EXTRACT, OUTPUT_SAFETY)
    g.add_edge(OUTPUT_SAFETY, PERSIST)
    g.add_edge(PERSIST, END)

    return g.compile()
