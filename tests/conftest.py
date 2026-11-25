"""测试夹具：全内存装配（MCP 适配器 + Skill 注册中心 + 执行器 + 图）。

不触网、不依赖 PG/Redis；MCP 的日历存储写到系统临时目录，不污染仓库。
"""
from __future__ import annotations

import tempfile

import pytest

from app.config import Settings
from app.core.retry import policy_from_settings
from app.core.singleflight import SingleFlight
from app.graph.context import GraphContext
from app.llm.embedder import HashEmbedder
from app.mcp.client import MCPClient
from app.mcp.registry import build_default_registry
from app.memory.vector_store import InMemoryVectorStore
from app.skills.builtin import build_builtin_skills
from app.skills.executor import SkillExecutor
from app.skills.registry import InMemorySkillStore, SkillRegistry
from app.storage.postgres import InMemoryRepository
from app.storage.redis_client import InMemoryCache
from tests.stub_llm import StubLLM


def make_settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        environment="test",
        llm_api_key="stub",
        trace_enabled=True,
        trace_preview_chars=200,
        singleflight_enabled=True,
        singleflight_wait_sec=2.0,
        graph_max_rounds=3,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
        checkpoint_backend="memory",
        skill_refresh_sec=0.0,
        memory_extract_enabled=True,
        mcp_state_dir=tempfile.mkdtemp(prefix="mp-mcp-"),
        admin_token="test-admin",
    )
    base.update(overrides)
    return Settings(**base)


async def make_ctx(llm: StubLLM | None = None, **settings_overrides) -> GraphContext:
    settings = make_settings(**settings_overrides)
    repo = InMemoryRepository()
    cache = InMemoryCache()
    embedder = HashEmbedder()
    vector_store = InMemoryVectorStore()

    mcp_registry = build_default_registry(settings)
    await mcp_registry.start_all()

    skills = SkillRegistry(
        InMemorySkillStore(),
        builtins=build_builtin_skills(),
        refresh_sec=0.0,
        plugin_packages=settings.plugin_packages,
    )
    await skills.bootstrap()

    singleflight = SingleFlight(
        cache,
        enabled=settings.singleflight_enabled,
        ttl=settings.singleflight_ttl,
        wait_timeout=settings.singleflight_wait_sec,
    )
    policy = policy_from_settings(settings)
    ctx = GraphContext(
        llm=llm or StubLLM(),
        embedder=embedder,
        repo=repo,
        cache=cache,
        vector_store=vector_store,
        settings=settings,
        skills=skills,
        executor=SkillExecutor(skills, singleflight=singleflight, policy=policy),
        mcp=MCPClient(mcp_registry, cache=cache, singleflight=singleflight, policy=policy),
        mcp_registry=mcp_registry,
        singleflight=singleflight,
        checkpointer_desc={"backend": "memory", "degraded": True},
    )
    return ctx


@pytest.fixture
async def ctx() -> GraphContext:
    context = await make_ctx()
    yield context
    await context.mcp_registry.close_all()


@pytest.fixture
async def ctx_and_llm():
    llm = StubLLM()
    context = await make_ctx(llm)
    yield context, llm
    await context.mcp_registry.close_all()
