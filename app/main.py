"""FastAPI 入口：装配依赖、启动主动推送后台任务。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.graph.context import GraphContext
from app.llm.clients import OpenAILLMClient
from app.llm.embedder import create_embedder
from app.memory.factory import create_vector_store
from app.scheduler.active_push import active_push_loop
from app.services.session_manager import SessionManager
from app.storage.object_store import ObjectStore
from app.storage.postgres import PostgresRepository, create_repository
from app.storage.redis_client import create_cache

logger = logging.getLogger(__name__)

# 前端构建产物目录（frontend/dist）；执行过 `npm run build` 后由 FastAPI 直接托管
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _validate_production(settings: Settings) -> None:
    """生产环境显式报错：禁止内存降级、缺失 Key / Secret / 向量库。"""
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
        missing.append("VECTOR_STORE_BACKEND（必须为 pgvector 或 milvus）")
    if settings.embedding_backend == "hash":
        missing.append("EMBEDDING_BACKEND（生产禁止 hash，应为 sentence-transformers 或 openai）")
    if settings.jwt_secret in ("", "dev-only-secret-change-me"):
        missing.append("JWT_SECRET")
    if missing:
        raise RuntimeError(
            "生产环境(ENVIRONMENT=prod)禁止使用默认值/内存降级，请先配置: " + ", ".join(missing)
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _setup_logging(settings)
    _validate_production(settings)

    if not settings.llm_api_key:
        raise RuntimeError("缺少 LLM_API_KEY：请在 .env 配置真实模型密钥（已移除 FakeLLM 演示回退）")

    llm = OpenAILLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
        model=settings.llm_model,
        image_model=settings.image_model,
    )
    repo = create_repository(settings.database_url)
    if isinstance(repo, PostgresRepository):
        await repo.init_db()
    cache = create_cache(settings.redis_url)
    embedder = create_embedder(settings)
    vector_store = create_vector_store(settings, repo, embedder.dim)
    await vector_store.init()  # pgvector: 建扩展+建表；memory 后端为无操作
    object_store = ObjectStore(local_dir=str(Path("data/objects")) if settings.environment != "prod" else None)

    ctx = GraphContext(
        llm=llm,
        embedder=embedder,
        repo=repo,
        cache=cache,
        vector_store=vector_store,
        settings=settings,
        object_store=object_store,
        notifier=None,  # 接入层可注入：WebSocket 管理器 / Webhook / 推送服务
    )
    app.state.ctx = ctx
    app.state.manager = SessionManager(ctx)

    push_task: asyncio.Task | None = None
    if settings.active_push_enabled:
        push_task = asyncio.create_task(active_push_loop(ctx))

    logger.info("%s started (env=%s)", settings.app_name, settings.environment)
    yield

    if push_task is not None:
        push_task.cancel()
    await repo.close()
    await cache.close()
    await vector_store.close()
    logger.info("shutdown complete")


app = FastAPI(
    title="AI Girlfriend Agent",
    description="LangGraph 八节点状态图：关系阶段 / 情绪 / 记忆 / 上下文全部入 State",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    # 存在前端构建产物时托管 Web 界面，否则返回 API 概览 JSON
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "name": "AI Girlfriend Agent",
        "docs": "/docs",
        "web": "frontend 未构建：cd frontend && npm install && npm run build 后访问本地址",
        "endpoints": [
            "POST /api/v1/auth/register",
            "POST /api/v1/auth/login",
            "GET  /api/v1/auth/me",
            "GET  /api/v1/session/list",
            "GET  /api/v1/session/detail?session_id=",
            "GET  /api/v1/session/history?session_id=",
            "POST /api/v1/chat",
            "POST /api/v1/chat/stream",
            "POST /api/v1/session/reset",
            "GET  /api/v1/memory/list",
            "POST /api/v1/memory/delete",
            "PUT  /api/v1/persona/update",
            "GET  /api/v1/health",
        ],
    }


if (FRONTEND_DIST / "index.html").exists():
    # 只挂载 /assets，不占用 /api /docs 等路由；静态资源走这里
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
