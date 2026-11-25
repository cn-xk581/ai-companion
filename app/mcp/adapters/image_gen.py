"""MCP 适配器：图像生成（OpenAI 兼容 images 接口）。

能力由外部模型服务提供；未配置模型时适配器明确报「能力未启用」，
由 Skill 转成「现在还不能发图」的用户话术——不伪造成功。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from app.core.errors import BusinessRuleViolation, NotFoundError, TransientError
from app.mcp.models import MCPAdapter, ToolSpec

logger = logging.getLogger(__name__)

ImageProvider = Callable[[str, str | None, str], Awaitable[str]]


class GenerateImageInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=600)
    style: str | None = Field(default=None, max_length=40)
    size: str = Field(default="1024x1024", pattern="^(512x512|1024x1024)$")


class ImageOutput(BaseModel):
    url: str
    prompt: str
    style: str | None = None


class ImageGenAdapter(MCPAdapter):
    name = "image_gen"
    version = "1.0.0"

    def __init__(self, provider: ImageProvider | None = None, timeout_sec: float = 25.0) -> None:
        self._provider = provider
        self._timeout = timeout_sec

    async def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="image.generate",
                adapter=self.name,
                description="按提示词生成一张图（返回 URL）",
                input_model=GenerateImageInput,
                output_model=ImageOutput,
                timeout_sec=self._timeout,
                cache_ttl=0,
                idempotent=False,  # 生成有随机性，不做合并缓存
                tags=["image", "write"],
            )
        ]

    async def invoke(self, tool: str, args: dict[str, Any]) -> Any:
        if tool != "image.generate":
            raise NotFoundError(f"image_gen 不支持工具 {tool}")
        payload = GenerateImageInput.model_validate(args)
        if self._provider is None:
            raise BusinessRuleViolation(
                "画图能力未启用：请配置 IMAGE_MODEL 与 LLM_API_KEY",
                detail={"tool": tool},
            )
        try:
            url = await self._provider(payload.prompt, payload.style, payload.size)
        except BusinessRuleViolation:
            raise
        except Exception as exc:  # noqa: BLE001 - 上游异常归一化，交给重试器分类
            raise TransientError(f"图像服务调用失败：{exc}") from exc
        if not url:
            raise TransientError("图像服务返回空结果")
        return {"url": url, "prompt": payload.prompt, "style": payload.style}

    async def health(self) -> bool:
        return self._provider is not None


def build_openai_image_provider(
    api_key: str, base_url: str | None, model: str
) -> ImageProvider:
    """把 OpenAI 兼容的 images 接口包装成 provider；key/model 缺失时不注册能力。"""

    async def _provider(prompt: str, style: str | None, size: str) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        full = f"{prompt}，风格：{style}" if style else prompt
        resp = await client.images.generate(model=model, prompt=full, size=size, n=1)
        return resp.data[0].url

    return _provider
