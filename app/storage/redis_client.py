"""缓存层：Redis 实现 + 内存降级实现。

用途：短期会话缓存、限流计数、会话锁、主动推送频率控制、幂等去重。

会话锁设计（分布式安全）：
- 获取锁时写入随机 token 作为持有者身份；
- 释放锁用 Lua「比较再删除」，只有持有者本人能释放，避免
  「处理超时 → 锁过期 → 别人拿到锁 → 我误删别人锁」的竞态；
- 租约 TTL 必须 > acquire 等待超时，且大于单轮最长处理耗时，
  由 Settings.lock_acquire_timeout_sec / lock_ttl_sec 对齐。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class Cache(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...

    @abstractmethod
    async def set_nx(self, key: str, value: str, ttl: int | None = None) -> bool:
        """仅当 key 不存在时写入，返回是否写入成功（原子抢占，用于幂等去重）。"""
        ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def incr(self, key: str, ttl: int | None = None) -> int: ...

    @abstractmethod
    async def close(self) -> None: ...

    @asynccontextmanager
    async def lock(self, key: str, timeout: float = 10.0, ttl: int = 120):
        """会话锁。timeout=等待获取锁的最长时间；ttl=锁租约秒数（须 > timeout）。"""
        token = uuid.uuid4().hex
        acquired = await self._acquire_lock(key, token, timeout, ttl)
        try:
            yield acquired
        finally:
            if acquired:
                await self._release_lock(key, token)

    @abstractmethod
    async def _acquire_lock(self, key: str, token: str, timeout: float, ttl: int) -> bool: ...

    @abstractmethod
    async def _release_lock(self, key: str, token: str) -> None: ...


# ================= Redis 实现 =================

class RedisCache(Cache):
    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._client.set(key, value, ex=ttl)

    async def set_nx(self, key: str, value: str, ttl: int | None = None) -> bool:
        return bool(await self._client.set(key, value, nx=True, ex=ttl))

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        if ttl:
            # 原子 INCR + 仅在首次创建时设置过期（固定窗口），避免并发下计数/过期竞态
            script = (
                "local v = redis.call('INCR', KEYS[1]) "
                "if v == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end "
                "return v"
            )
            return int(await self._client.eval(script, 1, key, ttl))
        return int(await self._client.incr(key))

    async def close(self) -> None:
        await self._client.aclose()

    async def _acquire_lock(self, key: str, token: str, timeout: float, ttl: int) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok = await self._client.set(key, token, nx=True, ex=ttl)
            if ok:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _release_lock(self, key: str, token: str) -> None:
        # 比较再删除：只有当前持有者能释放，避免误删后来者的锁
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        await self._client.eval(script, 1, key, token)


# ================= 内存实现（降级） =================

class InMemoryCache(Cache):
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}  # key -> (value, expire_at)
        self._counters: dict[str, tuple[int, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _expired(self, expire_at: float) -> bool:
        return expire_at != 0 and time.monotonic() > expire_at

    def _clean(self, key: str) -> None:
        entry = self._data.get(key)
        if entry and self._expired(entry[1]):
            self._data.pop(key, None)
        counter = self._counters.get(key)
        if counter and self._expired(counter[1]):
            self._counters.pop(key, None)

    async def get(self, key: str) -> str | None:
        self._clean(key)
        entry = self._data.get(key)
        return entry[0] if entry else None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._data[key] = (value, time.monotonic() + ttl if ttl else 0)

    async def set_nx(self, key: str, value: str, ttl: int | None = None) -> bool:
        self._clean(key)
        if key in self._data:
            return False
        self._data[key] = (value, time.monotonic() + ttl if ttl else 0)
        return True

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._counters.pop(key, None)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        self._clean(key)
        count, expire_at = self._counters.get(key, (0, 0))
        count += 1
        if ttl and expire_at == 0:
            expire_at = time.monotonic() + ttl
        self._counters[key] = (count, expire_at)
        return count

    async def close(self) -> None:
        return None

    async def _acquire_lock(self, key: str, token: str, timeout: float, ttl: int) -> bool:
        # 进程内 asyncio.Lock 天然持有者安全，无 TTL 自动过期（演示/单机场景可接受）
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _release_lock(self, key: str, token: str) -> None:
        lock = self._locks.get(key)
        if lock and lock.locked():
            lock.release()


def create_cache(redis_url: str) -> Cache:
    if redis_url:
        return RedisCache(redis_url)
    logger.warning("REDIS_URL 未配置，使用进程内内存缓存（仅演示/测试）")
    return InMemoryCache()
