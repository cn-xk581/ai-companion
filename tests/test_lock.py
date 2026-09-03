"""会话锁：token 所有权释放 + set_nx 原子抢占语义。"""
from __future__ import annotations

import asyncio

from app.storage.redis_client import InMemoryCache, RedisCache


async def test_inmemory_set_nx_semantics():
    c = InMemoryCache()
    assert await c.set_nx("k", "1", ttl=60) is True
    assert await c.set_nx("k", "2", ttl=60) is False
    assert await c.get("k") == "1"


async def test_inmemory_lock_serializes_and_releases():
    c = InMemoryCache()
    order: list[str] = []

    async def worker(tag: str):
        async with c.lock("lock:s", timeout=2, ttl=30) as acquired:
            assert acquired is True
            order.append(tag)
            await asyncio.sleep(0.05)

    await asyncio.gather(worker("a"), worker("b"))
    assert set(order) == {"a", "b"}
    # 锁已释放，可再次获取
    async with c.lock("lock:s", timeout=1, ttl=30) as acquired:
        assert acquired is True


class _FakeRedisClient:
    """复刻 Redis 相关命令，用于验证释放锁的 compare-and-delete 语义。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        return self.store.pop(key, None) is not None

    async def eval(self, script, numkeys, *args):
        # 与 redis_client 中 Lua 脚本语义一致：值匹配才删除
        key, token = args
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


def _make_redis_cache() -> RedisCache:
    rc = RedisCache.__new__(RedisCache)
    rc._client = _FakeRedisClient()
    return rc


async def test_redis_lock_release_only_by_owner():
    """修复点：锁过期后被别人重新获取时，旧持有者不能误删新持有者的锁。"""
    rc = _make_redis_cache()
    assert await rc._acquire_lock("lock:s1", "token-a", timeout=1, ttl=30) is True

    # 模拟锁过期后被 token-b 重新获取（直接覆盖值）
    rc._client.store["lock:s1"] = "token-b"

    # 旧持有者 token-a 释放：不得删除 token-b 的锁
    await rc._release_lock("lock:s1", "token-a")
    assert rc._client.store["lock:s1"] == "token-b"

    # 真正的持有者 token-b 释放：删除成功
    await rc._release_lock("lock:s1", "token-b")
    assert "lock:s1" not in rc._client.store


async def test_redis_lock_acquire_times_out_when_held():
    rc = _make_redis_cache()
    assert await rc._acquire_lock("lock:s2", "token-a", timeout=1, ttl=30) is True
    # 第二个持有者在锁仍被占有时获取失败
    assert await rc._acquire_lock("lock:s2", "token-b", timeout=0.2, ttl=30) is False
