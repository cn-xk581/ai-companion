"""calendar_remind：从长期记忆里查纪念日/重要日期，计算倒计时提醒。"""
from __future__ import annotations

import re
from datetime import date

from app.graph.context import GraphContext

SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_remind",
        "description": "查询纪念日/重要日期提醒：用户问『今天是什么日子』『我们的纪念日』『在一起多久了』时调用。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _parse_date(text: str) -> date | None:
    m = re.search(r"(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})[-月/.](\d{1,2})", text)
    if m:
        today = date.today()
        try:
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


async def handler(args: dict, ctx: GraphContext, session_id: str) -> str:
    memories = await ctx.repo.list_memories(session_id)
    anniversaries = [m for m in memories if m.category == "anniversary"]
    if not anniversaries:
        return "我这儿还没有记录任何纪念日呢。等你告诉我一个特别的日子，我会一直记得的。"

    today = date.today()
    lines: list[str] = []
    for m in anniversaries:
        d = _parse_date(m.content)
        if not d:
            continue
        next_date = d.replace(year=today.year)
        if next_date < today:
            next_date = next_date.replace(year=today.year + 1)
        days = (next_date - today).days
        label = "就是今天！" if days == 0 else f"还有 {days} 天"
        lines.append(f"- {m.content}（{label}）")
    if not lines:
        return "我记了一些日子，但格式有点乱，等你想起来再告诉我一次好不好？"
    return "我帮你数了数最近的日子：\n" + "\n".join(lines)
