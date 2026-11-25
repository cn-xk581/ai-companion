"""Checkpointer 工厂：给 LangGraph 提供「状态持久化 + 断点续跑」。

三档后端（按可用性自动降级，绝不因为缺配置而拒绝启动）
--------------------------------------------------
| 配置 | 后端 | 适用 |
|---|---|---|
| DATABASE_URL | AsyncPostgresSaver | 生产：多副本共享、崩了换台机器继续跑 |
| 无 PG | AsyncSqliteSaver（文件） | 单机 / 演示：进程重启仍能续跑 |
| 显式 memory 或降级 | InMemorySaver | 单测：进程内可中断恢复 |

关键约定
--------
- **thread_id = task_id**：一条任务一个线程，恢复时用同一个 thread_id 再 invoke 即可；
- Postgres 需要先 `setup()` 建表（幂等）；
- 连接串要转成 psycopg 可识别的 libpq 形式（去掉 SQLAlchemy 的 `+asyncpg`）；
- 恢复语义由 LangGraph 保证：已完成节点的 checkpoint 直接复用，不会重跑。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class CheckpointerHandle:
    saver: Any
    backend: str
    degraded: bool = False

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "degraded": self.degraded}


def _with_local_sslmode(url: str) -> str:
    """本机 PG 不需要 TLS；显式 sslmode=disable，避免 libpq 读取 ~/.postgresql 下的默认证书。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if (parts.hostname or "") not in {"127.0.0.1", "localhost", "::1"}:
        return url
    query = dict(parse_qsl(parts.query))
    query.setdefault("sslmode", "disable")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _libpq_url(database_url: str) -> str:
    """SQLAlchemy 风格 -> libpq 风格：postgresql+asyncpg:// -> postgresql://"""
    if "+asyncpg" in database_url:
        return database_url.replace("+asyncpg", "")
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql://", 1)
    return database_url


@asynccontextmanager
async def open_checkpointer(settings) -> AsyncIterator[CheckpointerHandle]:
    backend = (settings.checkpoint_backend or "auto").lower()

    if backend in ("auto", "postgres") and settings.database_url:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            url = _with_local_sslmode(_libpq_url(settings.database_url))
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                await saver.setup()
                logger.info("[checkpoint] 使用 PostgresSaver")
                yield CheckpointerHandle(saver=saver, backend="postgres")
            return
        except Exception as exc:  # noqa: BLE001 - 降级不阻塞启动，但必须显式告警
            logger.error("[checkpoint] PostgresSaver 不可用，降级：%s", exc)
            if backend == "postgres":
                raise

    if backend in ("auto", "sqlite") or not backend:
        path = Path(settings.checkpoint_sqlite_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
                # 探针：立刻读一次空 thread。aiosqlite 版本不兼容（如 0.21+ 移除了
                # Connection.is_alive）会在这里暴露，而不是等第一个任务跑到一半才炸。
                await saver.setup()
                await saver.aget_tuple({"configurable": {"thread_id": "__probe__:boot"}})
                logger.info("[checkpoint] 使用 AsyncSqliteSaver path=%s", path)
                yield CheckpointerHandle(saver=saver, backend="sqlite")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("[checkpoint] SqliteSaver 不可用（path=%s），降级内存：%s", path, exc)
            if backend == "sqlite":
                raise

    from langgraph.checkpoint.memory import InMemorySaver

    logger.warning("[checkpoint] 使用 InMemorySaver：进程重启后无法续跑（仅测试/演示）")
    yield CheckpointerHandle(saver=InMemorySaver(), backend="memory", degraded=True)


def thread_config(task_id: str, user_id: str) -> dict[str, Any]:
    """线程隔离：user 前缀防止不同租户猜 task_id 拿到别人的检查点。"""
    return {"configurable": {"thread_id": f"{user_id}:{task_id}"}}


def checkpoint_env_hint() -> str:
    return os.environ.get("CHECKPOINT_BACKEND", "auto")
