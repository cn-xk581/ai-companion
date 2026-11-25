"""FastAPI 入口：装配 MCP / Skill 注册中心 / 执行器 / 检查点 / 图。

启动顺序（顺序有依赖，别随意调整）
--------------------------------
1. 存储与缓存（PG / Redis / 内存降级）
2. MCP 适配器（表情库先加载静态数据；日历/画图按需初始化）
3. Skill 注册中心（同步内置 Skill → 运管开关保留）
4. 执行器 / SingleFlight / MCP 客户端（横切能力组装）
5. Checkpointer（PG / SQLite / 内存三档自动降级）
6. 图 + ChatService（图必须用同一个 checkpointer 实例编译）

优雅关闭：MCP 适配器 → 注册中心连接 → 向量库 → 缓存 → 仓库。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router, service_info
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.core.checkpointing import open_checkpointer
from app.core.retry import policy_from_settings
from app.core.singleflight import SingleFlight
from app.graph.builder import build_graph
from app.graph.context import GraphContext
from app.llm.clients import OpenAILLMClient, UnavailableLLM
from app.llm.embedder import create_embedder
from app.mcp.client import MCPClient
from app.mcp.registry import build_default_registry
from app.memory.factory import create_vector_store
from app.services.chat_service import ChatService
from app.skills.builtin import build_builtin_skills
from app.skills.executor import SkillExecutor
from app.skills.registry import SkillRegistry, create_skill_store
from app.storage.object_store import ObjectStore
from app.storage.postgres import PostgresRepository, create_repository
from app.storage.redis_client import create_cache

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = ["*"]


def _setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _validate_production(settings: Settings) -> None:
    """生产环境拒绝「演示配置」：内存降级、缺 key、默认密钥、内存检查点。"""
    if settings.environment != "prod":
        return
    missing: list[str] = []
    if not settings.llm_api_key:
        missing.append("LLM_API_KEY")
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if not settings.redis_url:
        missing.append("REDIS_URL")
    if settings.vector_store_backend not in ("pgvector", "milvus"):
        missing.append("VECTOR_STORE_BACKEND（pgvector 或 milvus）")
    if settings.embedding_backend == "hash":
        missing.append("EMBEDDING_BACKEND（禁止 hash）")
    if settings.jwt_secret in ("", "dev-only-secret-change-me"):
        missing.append("JWT_SECRET")
    if settings.checkpoint_backend == "memory":
        missing.append("CHECKPOINT_BACKEND（生产禁止 memory，无法断点续跑）")
    if not settings.admin_token:
        missing.append("ADMIN_TOKEN（Skill 运维接口需要）")
    if missing:
        raise RuntimeError("生产环境配置不完整，拒绝启动：" + "、".join(missing))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _setup_logging(settings)
    _validate_production(settings)

    # ---- 1) 存储 / 缓存 / 向量库 ----
    llm = (
        OpenAILLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            model=settings.llm_model,
        )
        if settings.llm_api_key
        else UnavailableLLM()
    )
    if not settings.llm_api_key:
        logger.warning("LLM_API_KEY 未配置：对话会走到「兜底话术 + 转人工」，不会启动失败")

    repo = create_repository(settings.database_url)
    if isinstance(repo, PostgresRepository):
        await repo.init_db()
    cache = create_cache(settings.redis_url)
    embedder = create_embedder(settings)
    vector_store = create_vector_store(settings, repo, embedder.dim)
    await vector_store.init()

    # ---- 2) MCP 适配器 ----
    mcp_registry = build_default_registry(settings)
    await mcp_registry.start_all()

    # ---- 3) Skill 注册中心 ----
    skill_store = create_skill_store(settings.redis_url)
    skills = SkillRegistry(
        skill_store,
        builtins=build_builtin_skills(),
        refresh_sec=settings.skill_refresh_sec,
        plugin_packages=settings.plugin_packages,
    )
    await skills.bootstrap()

    # ---- 4) 横切：SingleFlight / MCP 客户端 / 执行器 ----
    singleflight = SingleFlight(
        cache,
        enabled=settings.singleflight_enabled,
        ttl=settings.singleflight_ttl,
        wait_timeout=settings.singleflight_wait_sec,
    )
    policy = policy_from_settings(settings)
    mcp_client = MCPClient(mcp_registry, cache=cache, singleflight=singleflight, policy=policy)
    executor = SkillExecutor(skills, singleflight=singleflight, policy=policy)

    # ---- 5) Checkpointer + 图 ----
    async with open_checkpointer(settings) as checkpoint:
        ctx = GraphContext(
            llm=llm,
            embedder=embedder,
            repo=repo,
            cache=cache,
            vector_store=vector_store,
            settings=settings,
            skills=skills,
            executor=executor,
            mcp=mcp_client,
            mcp_registry=mcp_registry,
            singleflight=singleflight,
            object_store=ObjectStore(local_dir="data/objects")
            if settings.environment != "prod"
            else None,
            checkpointer_desc=checkpoint.describe(),
        )
        graph = build_graph(ctx, checkpoint.saver)
        app.state.ctx = ctx
        app.state.graph = graph
        app.state.chat = ChatService(ctx, graph, checkpoint.describe())

        logger.info(
            "%s started (env=%s, checkpoint=%s, skills=%d, mcp_tools=%d)",
            settings.app_name,
            settings.environment,
            checkpoint.backend,
            len(await skills.list_public()),
            len(mcp_registry.specs()),
        )
        try:
            yield
        finally:
            logger.info("shutdown: closing adapters / stores")
            await mcp_registry.close_all()
            await skill_store.close()
            await vector_store.close()
            await cache.close()
            await repo.close()


app = FastAPI(
    title="AI Companion Agent",
    description=(
        "AI 伴侣垂直 Agent：Skill/MCP 分层 + 多 Agent 能力隔离 + Redis Skill 注册中心 + "
        "LangGraph 检查点续跑 + SingleFlight 去重 + 错误分类重试 + 全链路 Trace"
    ),
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(auth_router)

# 前端构建产物（frontend/dist）：存在就直接托管，方便「单机单进程」部署；
# 不存在时 GET / 返回接口索引（开发阶段常用 vite dev，不需要它）。
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/", include_in_schema=False)
async def root():
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return service_info(get_settings())


if (FRONTEND_DIST / "index.html").exists():
    # 只挂 /assets，避免占用 /api、/docs 等路由
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
