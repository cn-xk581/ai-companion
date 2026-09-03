"""StubLLM：测试专用替身（不是功能，仅让离线测试确定性运行，不触网）。"""
from __future__ import annotations

import json

from app.llm.base import ImageResult, LLMClient, LLMResult, ToolCall


class StubLLM(LLMClient):
    def __init__(
        self,
        reply: str = "嗯哼，我在呢～你今天过得怎么样？",
        planner_tool: str | None = None,
        planner_args: dict | None = None,
        extract_memories: list[dict] | None = None,
        push_reply: str = "今天天气不错，要不要一起看部电影？",
    ) -> None:
        self.reply = reply
        self.planner_tool = planner_tool
        self.planner_args = planner_args or {}
        self.extract_memories = extract_memories if extract_memories is not None else []
        self.push_reply = push_reply
        self.complete_calls: list[list[dict]] = []
        self.stream_calls: list[list[dict]] = []

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:
        self.complete_calls.append(messages)
        system = messages[0]["content"] if messages else ""

        if "记忆管理员" in system:
            return LLMResult(content=json.dumps({"memories": self.extract_memories}, ensure_ascii=False))
        if "规划器" in system:
            if self.planner_tool:
                return LLMResult(content="", tool_calls=[ToolCall(name=self.planner_tool, arguments=self.planner_args)])
            return LLMResult(content='{"need_tool_call": false}')
        if "主动找话题" in system:
            return LLMResult(content=self.push_reply)

        return LLMResult(content=self.reply)

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
    ):
        self.stream_calls.append(messages)
        for i in range(0, len(self.reply), 4):
            yield self.reply[i : i + 4]

    async def generate_image(self, prompt: str, style: str | None = None) -> ImageResult:
        return ImageResult(url="http://stub.local/img.png")
