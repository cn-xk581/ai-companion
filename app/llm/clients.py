"""对话 LLM 客户端实现：OpenAI 兼容接口（DeepSeek / Qwen / Moonshot / OpenAI ...）。

注意：embedding 已解耦到 app.llm.embedder，本文件只负责对话/文生图。
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.llm.base import ImageResult, LLMClient, LLMResult, ToolCall


def _parse_tool_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


class OpenAILLMClient(LLMClient):
    """基于 OpenAI SDK 的兼容客户端。base_url 可指向任意 OpenAI 兼容网关。"""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "deepseek-chat",
        image_model: str | None = None,
    ):
        self._client = AsyncOpenAI(api_key=api_key or "sk-void", base_url=base_url or None)
        self.model = model
        self.image_model = image_model or None

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_format:
            kwargs["response_format"] = response_format

        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = [
            ToolCall(name=tc.function.name, arguments=_parse_tool_args(tc.function.arguments))
            for tc in (msg.tool_calls or [])
        ]
        usage = resp.usage.model_dump() if resp.usage else {}
        return LLMResult(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            raw=resp,
        )

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = 1024,
    ):
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    async def generate_image(self, prompt: str, style: str | None = None) -> ImageResult:
        if not self.image_model:
            return ImageResult(error="image model not configured（请在 .env 配置 IMAGE_MODEL）")
        try:
            full_prompt = f"{prompt}，风格：{style}" if style else prompt
            resp = await self._client.images.generate(
                model=self.image_model, prompt=full_prompt, size="1024x1024", n=1
            )
            return ImageResult(url=resp.data[0].url)
        except Exception as exc:  # noqa: BLE001
            return ImageResult(error=str(exc))


class UnavailableLLM(LLMClient):
    """未配置 LLM_API_KEY 时的占位实现。

    工程取舍：**不让服务起不来**。缺 key 时对话会：
    1) plan 节点收到 TransientError → 不调工具，按纯聊天继续；
    2) respond 节点流式失败 → 用兜底话术回复（再经 reply.polish 润色）；
    这样健康检查、Skill 运维、提醒/记忆链路仍然可用，便于灰度与联调。
    """

    model = "unavailable"

    async def complete(self, *args, **kwargs) -> LLMResult:  # noqa: D401
        from app.core.errors import TransientError

        raise TransientError("LLM 未配置：请设置 LLM_API_KEY")

    async def stream(self, *args, **kwargs):
        from app.core.errors import TransientError

        raise TransientError("LLM 未配置：请设置 LLM_API_KEY")
        yield ""  # pragma: no cover

    async def generate_image(self, prompt: str, style: str | None = None) -> ImageResult:
        return ImageResult(error="LLM 未配置")
