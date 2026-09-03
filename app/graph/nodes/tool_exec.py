"""节点 4：tool_exec_node 工具执行。

执行 planner 决策出的工具（generate_image / truth_or_dare / calendar_remind / send_meme），
结果回写 tool_results，然后流转到生成回复节点。
"""
from __future__ import annotations

from app.graph.context import GraphContext
from app.graph.state import AgentState
from app.tools.registry import execute_tool


async def tool_exec_node(state: AgentState, ctx: GraphContext) -> dict:
    results = []
    for call in state.tool_calls:
        result = await execute_tool(call.name, call.arguments, ctx, state.session_id)
        results.append(result)
    return {"tool_results": results, "need_tool_call": False}
