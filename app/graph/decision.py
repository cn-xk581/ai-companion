"""LLM 的合法输出结构：这一轮要不要用工具、用什么参数。

她的大部分回复不需要工具；只有「记个提醒」「画张图」「发个表情」这类
明确诉求才产出 tool_calls。参数仍要过 Skill 的 Input 模型，模型说错就拦。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlannedToolCall(BaseModel):
    skill: str = Field(min_length=3, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=120)


class TurnPlan(BaseModel):
    thoughts: str = Field(default="", max_length=400)
    tone_hint: str = Field(default="", max_length=60)
    tool_calls: list[PlannedToolCall] = Field(default_factory=list, max_length=3)

    @property
    def need_tool(self) -> bool:
        return bool(self.tool_calls)


REPAIR_HINT = (
    "上一次输出不符合约定。只输出一个 JSON 对象，字段：thoughts(string)、tone_hint(string)、"
    "tool_calls(array of {skill,args,reason})。skill 只能从给定清单里选；不需要工具就给空数组。"
)
