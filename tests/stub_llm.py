"""StubLLM：测试替身，扮演「工具决策器」「她」「记忆抽取器」，不触网。"""
from __future__ import annotations

import json
from typing import Any

from app.core.errors import TransientError
from app.llm.base import ImageResult, LLMClient, LLMResult


class StubLLM(LLMClient):
    def __init__(
        self,
        *,
        draft: str = "嗯，我在呢。今天过得怎么样？",
        tool_calls: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
        fail_stream: bool = False,
        fail_complete: bool = False,
    ) -> None:
        self.draft = draft
        self.tool_calls = tool_calls if tool_calls is not None else []
        self.memories = memories if memories is not None else []
        self.fail_stream = fail_stream
        self.fail_complete = fail_complete
        self.complete_calls: list[list[dict]] = []
        self.stream_calls: list[list[dict]] = []
        self.model = "stub-llm"

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:
        self.complete_calls.append(messages)
        if self.fail_complete:
            raise TransientError("stub: LLM 不可用")
        system = messages[0]["content"] if messages else ""
        if "工具决策器" in system:
            payload = {"thoughts": "按需调用", "tone_hint": "", "tool_calls": self.tool_calls}
        elif "抽取" in system:
            payload = {"memories": self.memories}
        else:
            payload = {}
        return LLMResult(
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 120, "completion_tokens": 40},
            finish_reason="stop",
        )

    async def stream(self, messages: list[dict], temperature: float = 0.8, max_tokens: int = 1024):
        self.stream_calls.append(messages)
        if self.fail_stream:
            raise TransientError("stub: 流式失败")
        for i in range(0, len(self.draft), 4):
            yield self.draft[i : i + 4]

    async def generate_image(self, prompt: str, style: str | None = None) -> ImageResult:
        return ImageResult(url="http://stub.local/x.png")
