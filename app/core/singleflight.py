"""SingleFlight：相同入参的请求合并，一进程内合并 + 跨进程（Redis）合并。

为什么必须做
------------
同一轮对话里同一句记忆召回 / 同一个提醒列表会被反复调用：
- 同一轮内并发多个 skill 调用会重复打同一个 MCP；
- 多轮 / 多副本（同一用户重试、多个 worker）会重复打上游，触发限流并放大 token 成本。

设计要点
--------
1. **两段式**：进程内用 Future 合并（零延迟）；跨进程用 Redis `SET NX` 抢「执行权」，
   抢到的执行并写结果，没抢到的轮询结果键（有超时），拿到即复用。
2. **fail-open**：等待超时或结果键缺失时，跟随者自己执行。宁可少合并，不可把请求挂死。
3. **只缓存成功结果**：失败必须让调用方自己感知并重试，避免把一次抖动放大成一片失败。
4. **key 必须稳定**：用 canonical_json（键序无关）+ skill 版本号，避免「同名不同实现」串味。
5. 结果为不可 JSON 化的对象时自动退化为「仅进程内合并」，不做跨进程缓存。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.core.errors import canonical_json
from app.storage.redis_client import Cache

logger = logging.getLogger(__name__)


def flight_key(namespace: str, payload: Any) -> str:
    """稳定 key：namespace + 规范化 JSON 的 sha1（长度可控，避免 Redis key 过长）。"""
    digest = hashlib.sha1(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


@dataclass
class FlightOutcome:
    value: Any
    #: 是否被别人合并（本调用没有真正执行）
    deduped: bool = False
    #: 是否抢到执行权
    leader: bool = False
    #: 跨进程合并命中
    cross_process: bool = False
    waited_ms: float = 0.0


class SingleFlight:
    def __init__(
        self,
        cache: Cache,
        *,
        enabled: bool = True,
        ttl: int = 120,
        wait_timeout: float = 8.0,
        poll_interval: float = 0.05,
        prefix: str = "sf",
    ) -> None:
        self._cache = cache
        self._enabled = enabled
        self._ttl = ttl
        self._wait_timeout = wait_timeout
        self._poll = poll_interval
        self._prefix = prefix
        self._inflight: dict[str, asyncio.Future] = {}
        # 观测指标：/health 与 Trace 用得上
        self.stats = {"leader": 0, "deduped_local": 0, "deduped_remote": 0, "fell_through": 0}

    async def run(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        ttl: int | None = None,
    ) -> FlightOutcome:
        """执行 factory()，相同 key 的并发调用只会真正执行一次。"""
        if not self._enabled:
            return FlightOutcome(value=await factory(), leader=True)

        started = time.monotonic()

        # ---- 1) 进程内合并 ----
        fut = self._inflight.get(key)
        if fut is not None:
            value = await asyncio.shield(fut)
            self.stats["deduped_local"] += 1
            return FlightOutcome(
                value=value,
                deduped=True,
                leader=False,
                waited_ms=round((time.monotonic() - started) * 1000, 1),
            )

        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            # ttl=None 表示「用默认值」；显式传 0 表示「只做进程内合并，不缓存结果」
            effective_ttl = self._ttl if ttl is None else ttl
            outcome = await self._run_coordinated(key, factory, effective_ttl)
            outcome.waited_ms = round((time.monotonic() - started) * 1000, 1)
            if not fut.done():
                fut.set_result(outcome.value)
            return outcome
        except BaseException as exc:  # noqa: BLE001 - 失败广播给所有等待者
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)

    async def _run_coordinated(
        self, key: str, factory: Callable[[], Awaitable[Any]], ttl: int
    ) -> FlightOutcome:
        if ttl <= 0:
            # 未开启结果缓存（如写操作 / 非幂等 Skill）：只做进程内合并，不碰 Redis，
            # 避免用 set_nx(ex=0) 这类非法参数，也避免把一次性结果广播给别的进程。
            return FlightOutcome(value=await factory(), leader=True)

        lock_key = f"{self._prefix}:lock:{key}"
        res_key = f"{self._prefix}:res:{key}"

        # 先看看有没有已经算好的结果（跨进程 / 上一位执行者留下的）
        cached = await self._read_result(res_key)
        if cached is not None:
            self.stats["deduped_remote"] += 1
            return FlightOutcome(value=cached, deduped=True, cross_process=True)

        acquired = await self._cache.set_nx(lock_key, "1", ttl=ttl)
        if not acquired:
            # 跟随者：轮询结果键，最多 wait_timeout
            deadline = time.monotonic() + self._wait_timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(self._poll)
                cached = await self._read_result(res_key)
                if cached is not None:
                    self.stats["deduped_remote"] += 1
                    return FlightOutcome(value=cached, deduped=True, cross_process=True)
            # 超时：fail-open，自己执行（不写结果键，避免覆盖领导者）
            self.stats["fell_through"] += 1
            logger.warning("[singleflight] 等待超时，自行执行 key=%s", key)
            return FlightOutcome(value=await factory(), leader=True)

        # 领导者：执行 + 写结果 + 放锁
        try:
            value = await factory()
            self._write_result(res_key, value, ttl)
            self.stats["leader"] += 1
            return FlightOutcome(value=value, leader=True)
        finally:
            await self._cache.delete(lock_key)

    async def _read_result(self, res_key: str) -> Any | None:
        raw = await self._cache.get(res_key)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict) or not payload.get("ok"):
            return None
        return payload.get("value")

    def _write_result(self, res_key: str, value: Any, ttl: int) -> None:
        try:
            encoded = json.dumps({"ok": True, "value": value}, ensure_ascii=False)
        except (TypeError, ValueError):
            # 不可序列化（如 pydantic 对象）：只保留进程内合并语义
            return
        # 这里不 await：结果写入是 best-effort，不能让它阻塞或改变主流程结果
        asyncio.create_task(self._cache.set(res_key, encoded, ttl=ttl))


__all__ = ["SingleFlight", "FlightOutcome", "flight_key"]
