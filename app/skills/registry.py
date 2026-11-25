"""Skill 注册中心：Redis 存元数据 + 版本号驱动的热更新（不重启服务）。

存储结构（Redis）
-----------------
| key | 类型 | 内容 |
|---|---|---|
| `skill:meta` | HASH | field=skill 名，value=SkillMeta JSON |
| `skill:rev`  | STRING | 全局版本号，任何写操作 +1 |

热更新机制
----------
不用 pub/sub（多副本/断线重连容易漏消息），改用**版本号轮询**：
- 每个进程记住上次看到的 `skill:rev` 与检查时间；
- 调用 `resolve()` 时，若距上次检查超过 refresh_sec 就 GET 一次 rev（一次 Redis 往返，极便宜）；
- rev 变了才 HGETALL 拉全量元数据。
- 管理接口写完后主动 `force_refresh()`，所以「本进程」立即生效，其他副本 ≤refresh_sec 生效。

动态上下线
----------
- `set_enabled(name, False)` → 之后该 Skill 解析即被拒（LLM 收到「能力已下线」的结构化错误）；
- `register(meta)` → 新增一条元数据，代码通过 `class_path` 动态 import（首次使用时加载）；
- 代码本身仍需随部署包发布（这是工程事实），但**启用/下线/改 schema 不需要重启**。
"""
from __future__ import annotations

import importlib
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.core.errors import BusinessRuleViolation, NotFoundError, ValidationFailed
from app.skills.base import Skill
from app.skills.models import SkillMeta

logger = logging.getLogger(__name__)

HASH_KEY = "skill:meta"
REV_KEY = "skill:rev"


class SkillStore(ABC):
    @abstractmethod
    async def put(self, meta: SkillMeta) -> None: ...

    @abstractmethod
    async def get(self, name: str) -> SkillMeta | None: ...

    @abstractmethod
    async def all(self) -> list[SkillMeta]: ...

    @abstractmethod
    async def delete(self, name: str) -> bool: ...

    @abstractmethod
    async def revision(self) -> int: ...

    async def close(self) -> None:  # pragma: no cover - 可选
        return None


class InMemorySkillStore(SkillStore):
    """测试 / 无 Redis 时的降级实现：语义与 Redis 版一致。"""

    def __init__(self) -> None:
        self._data: dict[str, SkillMeta] = {}
        self._rev = 0

    async def put(self, meta: SkillMeta) -> None:
        self._data[meta.name] = meta
        self._rev += 1

    async def get(self, name: str) -> SkillMeta | None:
        return self._data.get(name)

    async def all(self) -> list[SkillMeta]:
        return list(self._data.values())

    async def delete(self, name: str) -> bool:
        if name in self._data:
            del self._data[name]
            self._rev += 1
            return True
        return False

    async def revision(self) -> int:
        return self._rev


class RedisSkillStore(SkillStore):
    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(redis_url, decode_responses=True)

    async def put(self, meta: SkillMeta) -> None:
        pipe = self._client.pipeline()
        pipe.hset(HASH_KEY, meta.name, meta.model_dump_json())
        pipe.incr(REV_KEY)
        await pipe.execute()

    async def get(self, name: str) -> SkillMeta | None:
        raw = await self._client.hget(HASH_KEY, name)
        return _parse(raw) if raw else None

    async def all(self) -> list[SkillMeta]:
        raw = await self._client.hgetall(HASH_KEY)
        metas: list[SkillMeta] = []
        for value in raw.values():
            meta = _parse(value)
            if meta is not None:
                metas.append(meta)
        return metas

    async def delete(self, name: str) -> bool:
        pipe = self._client.pipeline()
        pipe.hdel(HASH_KEY, name)
        pipe.incr(REV_KEY)
        removed, _ = await pipe.execute()
        return bool(removed)

    async def revision(self) -> int:
        raw = await self._client.get(REV_KEY)
        return int(raw or 0)

    async def close(self) -> None:
        await self._client.aclose()


def _parse(raw: str) -> SkillMeta | None:
    try:
        return SkillMeta.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - 脏元数据不能拖垮整个注册中心
        logger.error("[skills] 元数据解析失败，已跳过：%s", exc)
        return None


def create_skill_store(redis_url: str) -> SkillStore:
    if redis_url:
        logger.info("[skills] 使用 Redis 注册中心")
        return RedisSkillStore(redis_url)
    logger.warning("[skills] 未配置 REDIS_URL，注册中心降级为进程内实现（多副本不共享）")
    return InMemorySkillStore()


class SkillRegistry:
    def __init__(
        self,
        store: SkillStore,
        *,
        builtins: list[Skill] | None = None,
        refresh_sec: float = 5.0,
        plugin_packages: list[str] | None = None,
    ) -> None:
        self._store = store
        self._builtins = builtins or []
        self._refresh_sec = max(0.0, refresh_sec)
        self._plugin_packages = plugin_packages or []
        self._cache: dict[str, SkillMeta] = {}
        self._revision = -1
        self._checked_at = 0.0
        self._instances: dict[str, Skill] = {}
        self._classes: dict[str, type[Skill]] = {}

    # ---------------- 启动 ----------------

    async def bootstrap(self) -> None:
        """把代码里的内置 Skill 同步进注册中心（保留已有的 enabled 开关）。"""
        for skill in self._builtins + self._load_plugins():
            meta = skill.meta()
            existing = await self._store.get(meta.name)
            if existing is not None:
                meta.enabled = existing.enabled          # 运营开关优先
                meta.owner = existing.owner or meta.owner
            await self._store.put(meta)
            self._instances[meta.name] = skill
            self._classes[meta.class_path] = type(skill)
            logger.info("[skills] 同步内置 Skill %s v%s enabled=%s", meta.name, meta.version, meta.enabled)
        await self.force_refresh()

    def _load_plugins(self) -> list[Skill]:
        skills: list[Skill] = []
        for pkg in self._plugin_packages:
            try:
                module = importlib.import_module(pkg)
                provided = getattr(module, "SKILLS", [])
                skills.extend([s for s in provided if isinstance(s, Skill)])
                logger.info("[skills] 插件包 %s 提供 %d 个 Skill", pkg, len(provided))
            except Exception as exc:  # noqa: BLE001 - 插件坏了不能阻断启动
                logger.error("[skills] 插件包 %s 加载失败：%s", pkg, exc)
        return skills

    # ---------------- 热更新 ----------------

    async def _ensure_fresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._checked_at) < self._refresh_sec:
            return
        try:
            rev = await self._store.revision()
        except Exception as exc:  # noqa: BLE001 - 注册中心抖动时用旧缓存继续服务
            logger.warning("[skills] 读版本号失败，沿用本地缓存：%s", exc)
            self._checked_at = now
            return
        self._checked_at = now
        if rev == self._revision and not force:
            return
        metas = await self._store.all()
        self._cache = {m.name: m for m in metas}
        self._revision = rev
        logger.info("[skills] 元数据已刷新 rev=%s 共 %d 个", rev, len(self._cache))

    async def force_refresh(self) -> None:
        await self._ensure_fresh(force=True)

    # ---------------- 解析 ----------------

    async def resolve(self, name: str) -> Skill:
        await self._ensure_fresh()
        meta = self._cache.get(name)
        if meta is None:
            raise NotFoundError(f"Skill 不存在：{name}", detail={"skill": name})
        if not meta.enabled:
            raise BusinessRuleViolation(
                f"Skill {name} 已下线",
                detail={"skill": name},
            )
        instance = self._instances.get(name)
        if instance is not None:
            return instance
        skill = self._instantiate(meta)
        self._instances[name] = skill
        return skill

    def _instantiate(self, meta: SkillMeta) -> Skill:
        cls = self._classes.get(meta.class_path)
        if cls is None:
            module_path, _, qualname = meta.class_path.partition(":")
            if not module_path or not qualname:
                raise ValidationFailed(
                    f"Skill {meta.name} 的 class_path 非法：{meta.class_path}",
                    detail={"skill": meta.name},
                )
            try:
                module = importlib.import_module(module_path)
                obj: Any = module
                for part in qualname.split("."):
                    obj = getattr(obj, part)
                cls = obj
            except Exception as exc:  # noqa: BLE001
                raise ValidationFailed(
                    f"Skill {meta.name} 代码加载失败：{exc}",
                    detail={"skill": meta.name, "class_path": meta.class_path},
                ) from exc
            if not (isinstance(cls, type) and issubclass(cls, Skill)):
                raise ValidationFailed(f"{meta.class_path} 不是 Skill 子类")
            self._classes[meta.class_path] = cls
        return cls()

    # ---------------- 运营操作 ----------------

    async def list_public(self) -> list[dict[str, Any]]:
        await self._ensure_fresh()
        return [m.public() for m in sorted(self._cache.values(), key=lambda m: m.name)]

    async def set_enabled(self, name: str, enabled: bool) -> SkillMeta:
        meta = await self._store.get(name)
        if meta is None:
            raise NotFoundError(f"Skill 不存在：{name}")
        meta.enabled = enabled
        meta.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self._store.put(meta)
        await self.force_refresh()
        self._instances.pop(name, None) if not enabled else None
        logger.info("[skills] %s 已%s", name, "上线" if enabled else "下线")
        return meta

    async def register(self, meta: SkillMeta) -> SkillMeta:
        if not meta.name or len(meta.name) < 3:
            raise ValidationFailed("Skill 名称至少 3 个字符")
        # 以代码为准：schema 从类上重新生成，避免元数据与实现不一致
        skill = self._instantiate(meta)
        regenerated = skill.meta()
        regenerated.enabled = meta.enabled
        regenerated.owner = meta.owner or "external"
        regenerated.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self._store.put(regenerated)
        await self.force_refresh()
        self._instances[regenerated.name] = skill
        return regenerated

    async def unregister(self, name: str) -> bool:
        removed = await self._store.delete(name)
        self._instances.pop(name, None)
        await self.force_refresh()
        return removed

    # ---------------- 观测 ----------------

    async def stats(self) -> dict[str, Any]:
        await self._ensure_fresh()
        return {
            "total": len(self._cache),
            "enabled": sum(1 for m in self._cache.values() if m.enabled),
            "revision": self._revision,
            "refresh_sec": self._refresh_sec,
            "cached_instances": len(self._instances),
        }
