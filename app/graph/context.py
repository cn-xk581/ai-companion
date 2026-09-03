"""GraphContext：把 LLM / 存储 / 向量库 / 配置注入整张图，节点通过闭包拿到依赖。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.config import Settings
from app.llm.base import LLMClient
from app.llm.embedder import Embedder
from app.memory.vector_store import VectorStore
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
    object_store: ObjectStore | None = None
    # 主动推送回调：接收 (session_id, text) -> 由接入层实现（WebSocket/Webhook/推送服务）
    notifier: Callable[[str, str], None] | None = None
    # 服务启动时的事件钩子（建表、建集合），由 main lifespan 调用
    startup_hooks: list[Callable] = field(default_factory=list)
