"""MCP 层的数据契约：只描述「外部资源怎么被调用」，不含任何业务规则。

边界纪律（评审重点）
--------------------
- MCP 适配器 **只做**：连接外部系统、协议转换、基础校验（类型/必填/超时）、错误归一化；
- MCP 适配器 **禁止**：判断「这个用户能不能吃花生」「热量够不够」「该选哪道菜」；
- 业务语义（过敏过滤、热量配比、预算判定）一律在 Skill 层。

因此这里的 ToolSpec 只声明：名字、入参模型（pydantic，自动产出 JSON Schema）、
是否幂等（决定能不能做 SingleFlight/缓存）、超时、缓存 TTL。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict


class ToolSpec(BaseModel):
    """一个可调用的外部能力（MCP 工具）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str                     # 形如 "nutrition.search"
    adapter: str                  # 归属适配器
    description: str
    input_model: type[BaseModel]  # 入参 pydantic 模型：既做校验，也生成 schema
    output_model: type[BaseModel] | None = None
    timeout_sec: float = 3.0
    cache_ttl: int = 0            # >0 才缓存（只对有稳定返回的只读工具开）
    idempotent: bool = True       # 幂等只读工具才允许 SingleFlight 合并
    tags: list[str] = field(default_factory=list)

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adapter": self.adapter,
            "description": self.description,
            "input_schema": self.input_schema(),
            "timeout_sec": self.timeout_sec,
            "cache_ttl": self.cache_ttl,
            "idempotent": self.idempotent,
            "tags": self.tags,
        }


@dataclass
class MCPResult:
    tool: str
    data: Any
    cached: bool = False
    deduped: bool = False
    attempts: int = 1
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "data": self.data,
            "cached": self.cached,
            "deduped": self.deduped,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
        }


class MCPAdapter(ABC):
    """外部资源适配器基类：一个适配器对接一个外部系统，暴露若干 ToolSpec。"""

    name: str = "adapter"
    version: str = "1.0.0"

    async def start(self) -> None:  # pragma: no cover - 可选钩子
        return None

    async def close(self) -> None:  # pragma: no cover - 可选钩子
        return None

    @abstractmethod
    async def tools(self) -> list[ToolSpec]: ...

    @abstractmethod
    async def invoke(self, tool: str, args: dict[str, Any]) -> Any:
        """执行一次外部调用。**必须**把外部异常包成 AppError（Transient/Validation 等）。"""

    async def health(self) -> bool:
        return True


def match_allow(name: str, allow: list[str] | None) -> bool:
    """白名单匹配，支持 `nutrition.*` 这类前缀通配；None 表示不限制。"""
    if allow is None:
        return True
    if "*" in allow:
        return True
    for item in allow:
        if item == name:
            return True
        if item.endswith(".*") and name.startswith(item[:-1]):
            return True
    return False


AdapterFactory = Callable[[Any], MCPAdapter]
