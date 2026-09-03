"""LLM / 多模态模型层抽象：对话、工具调用、embedding、文生图。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)
    raw: Any = None


@dataclass
class ImageResult:
    url: str | None = None
    b64: str | None = None
    error: str | None = None


class LLMClient(ABC):
    """统一模型层接口。生产用 OpenAILLMClient，离线测试用 tests/stub_llm.StubLLM。"""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult: ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """流式生成：产出文本增量。"""
        ...  # pragma: no cover

    @abstractmethod
    async def generate_image(self, prompt: str, style: str | None = None) -> ImageResult: ...
