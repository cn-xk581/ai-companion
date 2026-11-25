"""Skill 基类与执行上下文。

写一个 Skill 的姿势
------------------
```python
class MySkill(Skill):
    name = "example.echo"
    description = "把一句话原样返回（最小示例）"
    mcp_tools = ["calendar.list"]   # 声明依赖，执行器据此审计
    Input = MyInput
    Output = MyOutput
    async def run(self, payload: MyInput, sctx: SkillContext) -> MyOutput:
        res = await sctx.call_mcp("calendar.list", {...}, purpose="查现有提醒")
        ...
```
约束
----
- `run` 里**只能**通过 `sctx.call_mcp` 访问外部资源（禁止直接 httpx/pg），
  否则绕过超时/重试/缓存/审计，也不受 MCP 白名单约束；
- 业务判断（这句能不能说、该不该提醒、尺度是否越界）写在 Skill 里，不要下沉到 MCP 适配器；
- 不合法输入一律抛 `ValidationFailed`，由执行器转成友好提示。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from app.agents.models import AgentProfile
from app.core.errors import PermissionDenied
from app.core.tracing import Tracer
from app.mcp.client import MCPClient
from app.mcp.models import MCPResult, match_allow
from app.skills.models import SkillMeta


@dataclass
class SkillContext:
    """一次 Skill 执行的依赖容器（按任务构造，不跨任务复用）。"""

    task_id: str
    user_id: str
    agent: AgentProfile
    mcp: MCPClient
    tracer: Tracer
    settings: Any
    repo: Any = None
    embedder: Any = None
    vector_store: Any = None
    llm: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    async def call_mcp(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        purpose: str = "",
        declared: list[str] | None = None,
    ) -> MCPResult:
        """Skill 访问外部资源的唯一入口：先校验「依赖声明」，再走 Agent 的 MCP 白名单。"""
        if declared is not None and not match_allow(tool, declared):
            raise PermissionDenied(
                f"Skill 未声明对 MCP 工具 {tool} 的依赖，已拦截",
                detail={"tool": tool, "declared": declared},
            )
        return await self.mcp.call(
            tool, args, allow=self.agent.allowed_mcp, tracer=self.tracer, purpose=purpose
        )


class Skill(ABC):
    """业务能力单元。同一个实例可被多个 Agent 复用（无状态）。"""

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    owner: str = "core"

    Input: type[BaseModel]
    Output: type[BaseModel] | None = None

    #: 依赖的 MCP 工具（执行器会校验：Skill 只能调自己声明过的资源）
    mcp_tools: list[str] = []
    tags: list[str] = ["business"]

    timeout_sec: float = 8.0
    retry_attempts: int = 1
    cache_ttl: int = 0
    idempotent: bool = True

    def meta(self) -> SkillMeta:
        """从类上自省出注册元数据：schema 由 pydantic 生成，避免手写漂移。"""
        cls = type(self)
        return SkillMeta(
            name=self.name,
            version=self.version,
            description=self.description,
            class_path=f"{cls.__module__}:{cls.__qualname__}",
            input_schema=self.Input.model_json_schema(),
            output_schema=self.Output.model_json_schema() if self.Output else {},
            mcp_tools=list(self.mcp_tools),
            tags=list(self.tags),
            owner=self.owner,
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            timeout_sec=self.timeout_sec,
            retry_attempts=self.retry_attempts,
            cache_ttl=self.cache_ttl,
            idempotent=self.idempotent,
        )

    @abstractmethod
    async def run(self, payload: Any, sctx: SkillContext) -> Any:
        """执行主体。payload 已经过 Input 模型校验；返回值会过 Output 模型校验。"""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Skill {self.name} v{self.version}>"
