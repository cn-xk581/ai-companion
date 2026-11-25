"""Skill: reminder.create —— 提醒/纪念日（业务校验后调用日历 MCP）。

业务规则
--------
- 时间必须是未来、且在 365 天内（太远记不住也没意义）；
- 每个用户最多 20 条待提醒，超了拒绝并给替代方案；
- 同标题 + 时间相近（±60s）视为重复，不重复创建；
- 标题去空白、限长，返回可读话术。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.core.errors import BusinessRuleViolation
from app.skills.base import Skill, SkillContext

MAX_ACTIVE = 20
MAX_AHEAD_DAYS = 365
DISPLAY_TZ = ZoneInfo("Asia/Shanghai")


class ReminderInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    remind_at: datetime
    note: str = Field(default="", max_length=300)


class ReminderOutput(BaseModel):
    reminder_id: str
    title: str
    remind_at: str
    display: str
    message: str


class ReminderCreateSkill(Skill):
    name = "reminder.create"
    version = "1.1.0"
    description = "创建提醒：校验时间/条数/重复，再写入日历 MCP，返回可读话术"
    owner = "companion-tools"
    tags = ["business", "tool"]
    Input = ReminderInput
    Output = ReminderOutput
    mcp_tools = ["calendar.create", "calendar.list"]
    timeout_sec = 6.0

    async def run(self, payload: ReminderInput, sctx: SkillContext) -> ReminderOutput:
        now = datetime.now(timezone.utc)
        remind_at = payload.remind_at
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
        remind_at = remind_at.astimezone(timezone.utc)

        if remind_at <= now:
            raise BusinessRuleViolation(
                "提醒时间已经过去了", user_hint="换一个还没到的时间吧"
            )
        if remind_at - now > timedelta(days=MAX_AHEAD_DAYS):
            raise BusinessRuleViolation("提醒时间太远了（超过一年）", user_hint="先记一年内的吧")

        title = payload.title.strip()
        existing = await sctx.call_mcp(
            "calendar.list",
            {"user_id": payload.user_id, "include_inactive": False},
            purpose="检查已有提醒",
            declared=self.mcp_tools,
        )
        items = existing.data.get("items") or []
        if len(items) >= MAX_ACTIVE:
            raise BusinessRuleViolation(
                f"待提醒已有 {len(items)} 条，超过上限 {MAX_ACTIVE}",
                user_hint="先删几条旧的，不然我会记混",
            )
        for item in items:
            same_title = str(item.get("title", "")).strip() == title
            try:
                same_time = abs(
                    (datetime.fromisoformat(str(item.get("remind_at"))).replace(tzinfo=timezone.utc)
                     - remind_at).total_seconds()
                ) <= 60
            except Exception:  # noqa: BLE001 - 脏数据不影响新建
                same_time = False
            if same_title and same_time:
                display = remind_at.astimezone(DISPLAY_TZ).strftime("%m月%d日 %H:%M")
                return ReminderOutput(
                    reminder_id=str(item.get("id", "")),
                    title=title,
                    remind_at=remind_at.isoformat(),
                    display=display,
                    message=f"{display} 的「{title}」我已经记着了，不用再提醒我一遍。",
                )

        created = await sctx.call_mcp(
            "calendar.create",
            {
                "user_id": payload.user_id,
                "title": title,
                "remind_at": remind_at.isoformat(),
                "note": payload.note,
            },
            purpose="创建提醒",
            declared=self.mcp_tools,
        )
        reminder = created.data.get("reminder") or {}
        display = remind_at.astimezone(DISPLAY_TZ).strftime("%m月%d日 %H:%M")
        await sctx.tracer.decision("reminder.created", title=title, at=display)
        return ReminderOutput(
            reminder_id=str(reminder.get("id", "")),
            title=title,
            remind_at=remind_at.isoformat(),
            display=display,
            message=f"好，{display} 我提醒你「{title}」。",
        )
