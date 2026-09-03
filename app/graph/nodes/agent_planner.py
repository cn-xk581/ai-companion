"""节点 3：agent_planner_node 规划器（LLM）。

输入：人设 + 当前情绪 + 关系阶段 + 聊天历史 + 检索记忆 + 用户消息。
输出决策二选一：
- 分支 A：不需要工具 -> generate_reply_node
- 分支 B：需要调用工具 -> tool_exec_node（条件边跳转）

支持两种模式：原生 function calling（优先）；模型不支持时退化到 JSON 决策。
"""
from __future__ import annotations

import json
import logging

from app.graph.context import GraphContext
from app.graph.state import AgentState, ToolCallRequest
from app.llm.prompts import build_planner_messages
from app.persona.models import MOOD_LABELS
from app.tools.registry import TOOL_SCHEMAS

logger = logging.getLogger(__name__)


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


async def agent_planner_node(state: AgentState, ctx: GraphContext) -> dict:
    mood_label = MOOD_LABELS.get(state.mood.current_mood, state.mood.current_mood)
    messages = build_planner_messages(
        state.persona,
        state.relationship_stage,
        mood_label,
        state.mood.mood_intensity,
        state.retrieved_memories,
        state.chat_history[-ctx.settings.history_window :],
        state.user_input,
    )
    try:
        result = await ctx.llm.complete(
            messages,
            temperature=0.3,
            max_tokens=300,
            tools=TOOL_SCHEMAS,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] planner failed, fallback to direct reply: %s", state.session_id, exc)
        return {"need_tool_call": False, "tool_calls": []}

    # 模式 1：原生 function calling
    if result.tool_calls:
        calls = [ToolCallRequest(name=tc.name, arguments=tc.arguments) for tc in result.tool_calls]
        return {"need_tool_call": True, "tool_calls": calls}

    # 模式 2：JSON 决策
    decision = _parse_json(result.content) or {}
    if decision.get("need_tool_call"):
        calls: list[ToolCallRequest] = []
        for item in decision.get("tool_calls") or []:
            if isinstance(item, dict) and item.get("name"):
                calls.append(
                    ToolCallRequest(name=str(item["name"]), arguments=item.get("arguments") or {})
                )
        if calls:
            return {"need_tool_call": True, "tool_calls": calls}
    return {"need_tool_call": False, "tool_calls": []}
