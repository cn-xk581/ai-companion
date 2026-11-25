"""MCP 适配器：表情包目录（文本 + emoji，可替换为图片 CDN / 内容库）。

只负责「按标签检索候选」，选哪一条、要不要发、什么频率 —— 都是 Skill 的业务判断。
"""
from __future__ import annotations

import json
import logging
import zlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import NotFoundError, ValidationFailed
from app.mcp.models import MCPAdapter, ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class MemePickInput(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=6, description="情绪/场景标签，如 comfort、shy、goodnight")
    limit: int = Field(default=3, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=50, description="换一批时递增")


class MemeItem(BaseModel):
    id: str
    emoji: str
    caption: str
    tags: list[str] = Field(default_factory=list)


class MemePickOutput(BaseModel):
    items: list[MemeItem] = Field(default_factory=list)
    matched_tags: list[str] = Field(default_factory=list)
    total: int = 0


class MemeAdapter(MCPAdapter):
    name = "meme_library"
    version = "1.0.0"

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._items: list[MemeItem] = []

    async def start(self) -> None:
        path = self._dir / "memes.json"
        if not path.exists():
            raise ValidationFailed(f"表情包数据集缺失：{path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._items = [MemeItem.model_validate(item) for item in raw.get("memes", [])]
        logger.info("[mcp:meme] 已加载 %d 条表情", len(self._items))

    async def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="meme.pick",
                adapter=self.name,
                description="按标签挑几条表情（文本+emoji），offset 用于换一批",
                input_model=MemePickInput,
                output_model=MemePickOutput,
                timeout_sec=1.0,
                cache_ttl=60,
                idempotent=True,
                tags=["meme", "read"],
            )
        ]

    async def invoke(self, tool: str, args: dict[str, Any]) -> Any:
        if tool != "meme.pick":
            raise NotFoundError(f"meme_library 不支持工具 {tool}")
        payload = MemePickInput.model_validate(args)
        tags = {t.lower() for t in payload.tags if t}
        candidates = [
            m for m in self._items if tags and tags & {t.lower() for t in m.tags}
        ]
        matched = sorted(tags)
        if not candidates:
            candidates = list(self._items)
            matched = []
        # 稳定排序 + 偏移轮换：同样的输入给同样的结果，offset+1 就换一批
        candidates.sort(key=lambda m: zlib.crc32(m.id.encode("utf-8")))
        if candidates:
            start = (payload.offset * payload.limit) % len(candidates)
            candidates = candidates[start:] + candidates[:start]
        return {
            "items": [c.model_dump() for c in candidates[: payload.limit]],
            "matched_tags": matched,
            "total": len(candidates),
        }

    async def health(self) -> bool:
        return bool(self._items)
