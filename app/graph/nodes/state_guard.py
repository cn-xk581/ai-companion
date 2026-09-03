"""节点 2：state_guard_node 状态守卫。

1. 校验用户输入是否超出当前关系阶段允许的亲密度尺度；
2. 检测提示注入攻击；触发风控则设置 reject_reason，路由到输出兜底节点，不再走 LLM；
3. 根据用户输入微弱更新 current_mood / mood_intensity（被哄变好、吵架变差）。
"""
from __future__ import annotations

from app.graph.context import GraphContext
from app.graph.state import AgentState
from app.safety.guard import check_input, update_mood


async def state_guard_node(state: AgentState, ctx: GraphContext) -> dict:
    reason = check_input(state.user_input, state.relationship_stage)
    mood = update_mood(state.user_input, state.mood)

    updates: dict = {
        "mood": mood,
        "guard_flags": {**state.guard_flags, "checked": True},
    }
    if reason:
        updates["reject_reason"] = reason
        updates["guard_flags"]["reject"] = reason
    return updates
