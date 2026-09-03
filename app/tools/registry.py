"""工具注册表：LLM 函数调用 schema + 执行分发。

统一签名：handler(args: dict, ctx: GraphContext, session_id: str) -> str
"""
from __future__ import annotations

from app.graph.context import GraphContext
from app.graph.state import ToolResult
from app.tools import calendar_remind, generate_image, send_meme, truth_or_dare

TOOL_SCHEMAS: list[dict] = [
    generate_image.SCHEMA,
    truth_or_dare.SCHEMA,
    calendar_remind.SCHEMA,
    send_meme.SCHEMA,
]

_HANDLERS = {
    "generate_image": generate_image.handler,
    "truth_or_dare": truth_or_dare.handler,
    "calendar_remind": calendar_remind.handler,
    "send_meme": send_meme.handler,
}


async def execute_tool(name: str, args: dict, ctx: GraphContext, session_id: str) -> ToolResult:
    handler = _HANDLERS.get(name)
    if handler is None:
        return ToolResult(name=name, success=False, output=f"未知工具：{name}")
    try:
        output = await handler(args or {}, ctx, session_id)
        return ToolResult(name=name, success=True, output=output)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(name=name, success=False, output=f"工具执行出错：{exc}")


def tool_context(results: list[ToolResult]) -> str:
    """把工具结果拼成给生成节点的上下文片段。"""
    if not results:
        return ""
    return "\n".join(f"- [{r.name}] {r.output}" for r in results)
