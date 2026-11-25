"""MCP 适配器注册表：新增外部能力 = 新增一个 Adapter + 注册，不改 Agent 图。

内置三个适配器：
- `calendar`     提醒/纪念日（本地 JSON 存储，可换飞书/Google Calendar）
- `image_gen`    画图（OpenAI 兼容 images，未配置模型时明确报能力未启用）
- `meme_library` 表情包目录（文本+emoji，可换图片 CDN）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.errors import NotFoundError
from app.mcp.models import MCPAdapter, ToolSpec

logger = logging.getLogger(__name__)


class MCPRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, MCPAdapter] = {}
        self._tools: dict[str, tuple[MCPAdapter, ToolSpec]] = {}
        self._started = False

    # ---------------- 生命周期 ----------------

    def register(self, adapter: MCPAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"adapter 重名：{adapter.name}")
        self._adapters[adapter.name] = adapter
        logger.info("[mcp] 注册适配器 %s (%s)", adapter.name, adapter.version)

    async def start_all(self) -> None:
        for adapter in self._adapters.values():
            await adapter.start()
        await self.refresh()
        self._started = True

    async def refresh(self) -> None:
        tools: dict[str, tuple[MCPAdapter, ToolSpec]] = {}
        for adapter in self._adapters.values():
            for spec in await adapter.tools():
                if spec.name in tools:
                    raise ValueError(f"MCP 工具重名：{spec.name}")
                tools[spec.name] = (adapter, spec)
        self._tools = tools

    async def close_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[mcp] 关闭适配器 %s 失败：%s", adapter.name, exc)

    # ---------------- 查询 ----------------

    def resolve(self, tool: str) -> tuple[MCPAdapter, ToolSpec]:
        entry = self._tools.get(tool)
        if entry is None:
            raise NotFoundError(f"未知 MCP 工具：{tool}", detail={"tool": tool})
        return entry

    def specs(self) -> list[ToolSpec]:
        return [spec for _, spec in self._tools.values()]

    def describe(self) -> list[dict[str, Any]]:
        return [spec.describe() for spec in self.specs()]

    async def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, adapter in self._adapters.items():
            try:
                result[name] = {"ok": bool(await adapter.health()), "tools": len(await adapter.tools())}
            except Exception as exc:  # noqa: BLE001
                result[name] = {"ok": False, "error": str(exc)}
        return {"started": self._started, "adapters": result, "tools": len(self._tools)}


def build_default_registry(settings) -> MCPRegistry:
    """装配内置适配器。外部服务地址/密钥全部来自配置，不硬编码。"""
    from app.mcp.adapters.calendar_local import CalendarAdapter
    from app.mcp.adapters.image_gen import ImageGenAdapter, build_openai_image_provider
    from app.mcp.adapters.meme_library import MemeAdapter

    state_dir = Path(settings.mcp_state_dir or "data/mcp")
    data_dir = Path(settings.mcp_data_dir) if settings.mcp_data_dir else None

    provider = None
    if settings.image_model and settings.llm_api_key:
        provider = build_openai_image_provider(
            settings.llm_api_key, settings.llm_base_url or None, settings.image_model
        )

    registry = MCPRegistry()
    registry.register(CalendarAdapter(store_path=state_dir / "calendar.json"))
    registry.register(ImageGenAdapter(provider=provider, timeout_sec=settings.mcp_http_timeout_sec * 8))
    registry.register(MemeAdapter(data_dir=data_dir))
    return registry
