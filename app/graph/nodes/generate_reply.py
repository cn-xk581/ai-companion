"""节点 5：generate_reply_node 生成最终回复（支持流式）。

构造完整 Prompt，分层拼接：
1. 固定人格 profile
2. 当前情绪、情绪强度
3. 当前关系阶段约束
4. 检索得到的过往记忆片段
5. 滑动窗口短期聊天历史
6. 用户最新输入（+ 工具执行结果，如有）

通过 LangGraph get_stream_writer 边生成边把 token 推给 SSE 通道；同时拼回完整回复写回 state。
"""
from __future__ import annotations

import logging

from langgraph.config import get_stream_writer

from app.graph.context import GraphContext
from app.graph.state import AgentState
from app.llm.prompts import build_generator_messages
from app.persona.models import MOOD_LABELS
from app.tools.registry import tool_context

logger = logging.getLogger(__name__)


async def generate_reply_node(state: AgentState, ctx: GraphContext) -> dict:
    mood_label = MOOD_LABELS.get(state.mood.current_mood, state.mood.current_mood)
    messages = build_generator_messages(
        state.persona,
        state.relationship_stage,
        mood_label,
        state.mood.mood_intensity,
        state.retrieved_memories,
        state.chat_history[-ctx.settings.history_window :],
        state.user_input,
        tool_context=tool_context(state.tool_results),
    )

    writer = get_stream_writer()
    try:
        parts: list[str] = []
        async for token in ctx.llm.stream(
            messages,
            temperature=ctx.settings.llm_temperature,
            max_tokens=ctx.settings.llm_max_tokens,
        ):
            parts.append(token)
            writer({"type": "token", "content": token})
        reply = "".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] generate reply failed: %s", state.session_id, exc)
        reply = ""

    return {"agent_reply": reply}
