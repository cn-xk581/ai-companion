"""MCP 适配器：本地日历 / 提醒服务（可替换为飞书、Google Calendar、企业微信）。

纪律：这里只做 CRUD 与时间格式归一化；「多久提醒一次合理」「标题是否合规」
「用户能不能建第 21 条」属于业务规则，在 Skill 层。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import NotFoundError
from app.mcp.models import MCPAdapter, ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_STORE = Path("data/mcp/calendar.json")


class Reminder(BaseModel):
    id: str
    user_id: str
    title: str
    remind_at: datetime
    note: str = ""
    status: str = "pending"  # pending | cancelled | done
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateReminderInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    remind_at: datetime
    note: str = Field(default="", max_length=300)


class ListRemindersInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    include_inactive: bool = False


class CancelReminderInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    reminder_id: str = Field(min_length=1, max_length=64)


class ReminderOutput(BaseModel):
    reminder: Reminder


class ReminderListOutput(BaseModel):
    items: list[Reminder] = Field(default_factory=list)


class CalendarAdapter(MCPAdapter):
    name = "calendar"
    version = "1.0.0"

    def __init__(self, store_path: str | Path | None = None) -> None:
        self._path = Path(store_path) if store_path else DEFAULT_STORE
        self._items: dict[str, Reminder] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for item in raw.get("reminders", []):
                    reminder = Reminder.model_validate(item)
                    self._items[reminder.id] = reminder
            except Exception as exc:  # noqa: BLE001 - 脏文件不能阻断启动
                logger.error("[mcp:calendar] 读取 %s 失败：%s", self._path, exc)
        logger.info("[mcp:calendar] 已加载 %d 条提醒（%s）", len(self._items), self._path)

    async def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="calendar.create",
                adapter=self.name,
                description="创建一条提醒（写入日历存储）",
                input_model=CreateReminderInput,
                output_model=ReminderOutput,
                timeout_sec=3.0,
                cache_ttl=0,
                idempotent=False,  # 写操作不参与 SingleFlight 合并
                tags=["calendar", "write"],
            ),
            ToolSpec(
                name="calendar.list",
                adapter=self.name,
                description="列出该用户的提醒",
                input_model=ListRemindersInput,
                output_model=ReminderListOutput,
                timeout_sec=2.0,
                # 写操作后必须立刻读到新数据：列表不做缓存（缓存失效比省这一次调用更重要）
                cache_ttl=0,
                idempotent=True,
                tags=["calendar", "read"],
            ),
            ToolSpec(
                name="calendar.cancel",
                adapter=self.name,
                description="取消一条提醒",
                input_model=CancelReminderInput,
                output_model=ReminderOutput,
                timeout_sec=3.0,
                cache_ttl=0,
                idempotent=False,
                tags=["calendar", "write"],
            ),
        ]

    async def invoke(self, tool: str, args: dict[str, Any]) -> Any:
        if tool == "calendar.create":
            return await self._create(CreateReminderInput.model_validate(args))
        if tool == "calendar.list":
            return await self._list(ListRemindersInput.model_validate(args))
        if tool == "calendar.cancel":
            return await self._cancel(CancelReminderInput.model_validate(args))
        raise NotFoundError(f"calendar 不支持工具 {tool}")

    # ---------------- 内部实现 ----------------

    async def _create(self, payload: CreateReminderInput) -> dict[str, Any]:
        async with self._lock:
            remind_at = payload.remind_at
            if remind_at.tzinfo is None:
                remind_at = remind_at.replace(tzinfo=timezone.utc)
            reminder = Reminder(
                id=f"rm_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                user_id=payload.user_id,
                title=payload.title,
                remind_at=remind_at.astimezone(timezone.utc),
                note=payload.note,
            )
            self._items[reminder.id] = reminder
            await self._flush()
            return {"reminder": reminder.model_dump(mode="json")}

    async def _list(self, payload: ListRemindersInput) -> dict[str, Any]:
        items = [
            r for r in self._items.values()
            if r.user_id == payload.user_id
            and (payload.include_inactive or r.status == "pending")
        ]
        items.sort(key=lambda r: r.remind_at)
        return {"items": [r.model_dump(mode="json") for r in items]}

    async def _cancel(self, payload: CancelReminderInput) -> dict[str, Any]:
        async with self._lock:
            reminder = self._items.get(payload.reminder_id)
            if reminder is None or reminder.user_id != payload.user_id:
                raise NotFoundError("提醒不存在", detail={"reminder_id": payload.reminder_id})
            reminder.status = "cancelled"
            await self._flush()
            return {"reminder": reminder.model_dump(mode="json")}

    async def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = {"reminders": [r.model_dump(mode="json") for r in self._items.values()]}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    async def health(self) -> bool:
        return True
