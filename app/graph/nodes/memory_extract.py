"""节点 6：memory_extract_node 记忆抽取。

交给 LLM 从本轮对话提取真正值得记住的事实（爱好/重要事件/小故事/纪念日），
打 importance 权重存入 new_memories。不重要的闲聊直接丢弃，避免记忆库垃圾膨胀。
"""
from __future__ import annotations

from app.graph.context import GraphContext
from app.graph.state import AgentState
from app.memory.extractor import extract_memories


async def memory_extract_node(state: AgentState, ctx: GraphContext) -> dict:
    if state.reject_reason or not state.agent_reply:
        return {"new_memories": []}
    memories = await extract_memories(
        ctx.llm, state.user_input, state.agent_reply, speaker=state.persona.name
    )
    return {"new_memories": memories}
