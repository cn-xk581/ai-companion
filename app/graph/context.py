"""GraphContext：图运行期的依赖容器（进程内单例，按任务派生 Tracer / SkillContext）。

为什么 Tracer 要按 task 存在 ctx 上
----------------------------------
Tracer 保存本次运行的 token 计数与 span 缓冲，是有状态对象；不能塞进 LangGraph State
（检查点只存可 JSON 化的数据）。所以由 runner 在开始/恢复任务时 `start_tracer()`，
同一任务内所有节点复用同一个实例，异常退出也不会把计数器写脏检查点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.models import AgentProfile
from app.config import Settings
from app.core.singleflight import SingleFlight
from app.core.tracing import Tracer, make_tracer
from app.llm.base import LLMClient
from app.llm.embedder import Embedder
from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry
from app.memory.vector_store import VectorStore
from app.skills.base import SkillContext
from app.skills.executor import SkillExecutor
from app.skills.registry import SkillRegistry
from app.storage.object_store import ObjectStore
from app.storage.postgres import Repository
from app.storage.redis_client import Cache


@dataclass
class GraphContext:
    llm: LLMClient
    embedder: Embedder
    repo: Repository
    cache: Cache
    vector_store: VectorStore
    settings: Settings

    skills: SkillRegistry
    executor: SkillExecutor
    mcp: MCPClient
    mcp_registry: MCPRegistry
    singleflight: SingleFlight

    object_store: ObjectStore | None = None
    notifier: Callable[[str, str], None] | None = None
    _tracers: dict[str, Tracer] = field(default_factory=dict)
    checkpointer_desc: dict[str, Any] = field(default_factory=dict)

    # ---------------- Tracer 生命周期 ----------------

    def start_tracer(self, task_id: str) -> Tracer:
        """任务开始 / 恢复时调用：重建内存计数器，事件仍逐条落库。"""

        async def sink(event: dict[str, Any]) -> None:
            await self.repo.append_event(task_id, event)

        tracer = make_tracer(task_id, sink, self.settings) if self.settings.trace_enabled else Tracer(
            task_id, None, enabled=False
        )
        self._tracers[task_id] = tracer
        return tracer

    def tracer_for(self, task_id: str) -> Tracer:
        tracer = self._tracers.get(task_id)
        return tracer if tracer is not None else self.start_tracer(task_id)

    def drop_tracer(self, task_id: str) -> None:
        self._tracers.pop(task_id, None)

    # ---------------- 派生 SkillContext ----------------

    def skill_context(self, task_id: str, user_id: str, agent: AgentProfile) -> SkillContext:
        return SkillContext(
            task_id=task_id,
            user_id=user_id,
            agent=agent,
            mcp=self.mcp,
            tracer=self.tracer_for(task_id),
            settings=self.settings,
            repo=self.repo,
            embedder=self.embedder,
            vector_store=self.vector_store,
            llm=self.llm,
        )
