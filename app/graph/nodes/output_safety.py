"""节点 7：output_safety_node 输出校验兜底。

1. 若被 state_guard 拦截（reject_reason）-> 直接换预设 fallback；
2. 若 agent_reply 为空（LLM 异常）-> fallback；
3. 检查回复是否越界 / 人设崩坏 -> 异常则替换为预设 fallback；
4. 限制输出长度。
"""
from __future__ import annotations

from app.graph.context import GraphContext
from app.graph.state import AgentState
from app.safety.output import apply_fallback, check_output, trim_reply


async def output_safety_node(state: AgentState, ctx: GraphContext) -> dict:
    reply = state.agent_reply

    if state.reject_reason:
        reply = apply_fallback(state.reject_reason)
    elif not reply or not reply.strip():
        reply = apply_fallback("llm_error")
    else:
        issue = check_output(reply)
        if issue is not None:
            reply = apply_fallback("output_safety")
        else:
            reply = trim_reply(reply, ctx.settings.max_reply_length)

    return {"agent_reply": reply}
