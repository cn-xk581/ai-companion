"""测试夹具：构建全内存 GraphContext（LLM 用 StubLLM 替身，不触网）。"""
from __future__ import annotations

from app.config import Settings
from app.graph.context import GraphContext
from app.llm.embedder import HashEmbedder
from app.memory.vector_store import InMemoryVectorStore
from app.storage.postgres import InMemoryRepository
from app.storage.redis_client import InMemoryCache
from tests.stub_llm import StubLLM


def make_ctx(**llm_kwargs) -> tuple[GraphContext, StubLLM]:
    llm = StubLLM(**llm_kwargs)
    ctx = GraphContext(
        llm=llm,
        embedder=HashEmbedder(),
        repo=InMemoryRepository(),
        cache=InMemoryCache(),
        vector_store=InMemoryVectorStore(),
        settings=Settings(_env_file=None),  # 隔离 .env，测试只依赖默认值
    )
    return ctx, llm
