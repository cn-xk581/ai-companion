"""Skill 层的契约：元数据、调用、结果。

与 MCP 的边界（评审红线）
-------------------------
| 关注点 | Skill | MCP |
|---|---|---|
| 业务规则（过敏、热量配比、预算） | ✅ | ❌ 禁止 |
| 入参/出参业务 Schema 校验 | ✅ | 只做类型/必填 |
| 权限判断（哪个 Agent 能用） | ✅ 执行器统一判 | 只判「资源是否授权」 |
| 连接外部系统、协议/错误归一化 | ❌ | ✅ |
| 预处理/后处理（单位换算、兜底） | ✅ | ❌ |

Skill 的输入输出都用 pydantic 模型声明：LLM 生成的参数先过 Schema，
不合法在**执行前**就被拦截，不会带着脏参数去调外部系统。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillMeta(BaseModel):
    """注册中心里存的东西：只有元数据，代码仍在部署包里（class_path 指过去）。"""

    name: str
    version: str = "1.0.0"
    description: str = ""
    class_path: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    mcp_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    owner: str = "core"
    updated_at: str = ""
    timeout_sec: float = 8.0
    retry_attempts: int = 1
    cache_ttl: int = 0
    idempotent: bool = True

    def public(self) -> dict[str, Any]:
        return self.model_dump()


class SkillCall(BaseModel):
    """LLM 决策出来的一次调用：只允许 {skill, args}，其余字段一律丢弃。"""

    skill: str = Field(min_length=3, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class SkillOutcome(BaseModel):
    """统一结果：**永远不抛异常**给图，失败也是结构化 outcome（LLM 可据此重规划）。"""

    skill: str
    ok: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    error_kind: str | None = None
    message: str = ""
    user_message: str = ""
    latency_ms: float = 0.0
    attempts: int = 1
    deduped: bool = False
    warnings: list[str] = Field(default_factory=list)

    def fail(self, kind: str, message: str, user_message: str = "") -> "SkillOutcome":
        self.ok = False
        self.error_kind = kind
        self.message = message
        self.user_message = user_message
        return self

    def public(self) -> dict[str, Any]:
        return self.model_dump()
