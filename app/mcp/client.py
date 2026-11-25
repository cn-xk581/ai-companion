"""MCP 客户端：所有外部资源的唯一入口（校验 → 缓存 → 合并 → 重试 → 审计）。

为什么不让 Skill 直接 httpx/pg
------------------------------
1. 外部调用的横切能力（超时、重试、合并、缓存、审计）如果散落在每个 Skill 里，
   一定会漏；集中到一个客户端才能「一处修，全局生效」；
2. 权限校验放在这里，可以挡住「Skill 代码偷偷调一个自己 Agent 没被授权的资源」；
3. 新增外部系统只加 Adapter，Skill 与 Agent 图不改。

调用链
------
Skill → MCPClient.call(tool, args)
  ├─ resolve(tool)               未知工具 → NotFound
  ├─ 白名单校验（agent.allowed_mcp）→ PermissionDenied
  ├─ 入参 pydantic 校验           → 不合法直接拦截（不触达外部系统）
  ├─ 读缓存（cache_ttl>0）
  ├─ SingleFlight（幂等工具）      相同入参并发合并
  ├─ RetryPolicy + 超时           仅对 TRANSIENT / RATE_LIMIT 重试
  ├─ 出参结构校验 → 写缓存 → 打 Trace
  └─ 异常统一归一化成 AppError
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.core.errors import (
    AppError,
    PermissionDenied,
    TransientError,
    ValidationFailed,
    classify,
    to_app_error,
)
from app.core.retry import RetryPolicy, call_with_retry
from app.core.singleflight import SingleFlight, flight_key
from app.core.tracing import Tracer
from app.mcp.models import MCPResult, match_allow
from app.mcp.registry import MCPRegistry
from app.storage.redis_client import Cache

logger = logging.getLogger(__name__)


class MCPClient:
    def __init__(
        self,
        registry: MCPRegistry,
        *,
        cache: Cache,
        singleflight: SingleFlight,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.registry = registry
        self._cache = cache
        self._sf = singleflight
        self._policy = policy or RetryPolicy()

    async def call(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        allow: list[str] | None = None,
        tracer: Tracer | None = None,
        purpose: str = "",
    ) -> MCPResult:
        args = args or {}
        adapter, spec = self.registry.resolve(tool)

        # ---- 1) 权限：Agent 的 allowed_mcp 决定能不能碰这个外部资源 ----
        if not match_allow(tool, allow):
            detail = {"tool": tool, "allow": allow}
            if tracer is not None:
                await tracer.decision(
                    "mcp.permission_denied", tool=tool, allow=allow, purpose=purpose
                )
            raise PermissionDenied(
                f"该 Agent 未被授权访问 MCP 工具 {tool}", detail=detail
            )

        # ---- 2) 入参校验：不合法绝不发出去 ----
        try:
            payload = spec.input_model.model_validate(args)
        except Exception as exc:  # noqa: BLE001
            raise ValidationFailed(
                f"MCP 工具 {tool} 入参不合法：{_short_errors(exc)}",
                detail={"tool": tool, "args": args, "errors": _validation_errors(exc)},
            ) from exc
        normalized = payload.model_dump()

        started = time.perf_counter()
        cache_key = flight_key(
            f"mcp:{tool}", {"adapter": adapter.version, "args": normalized}
        )
        span_kwargs = {"tool": tool, "adapter": adapter.name, "purpose": purpose, "args": normalized}

        # ---- 3) 缓存：只读且声明了 TTL 的工具才缓存 ----
        if spec.cache_ttl > 0:
            cached = await self._read_cache(cache_key, tool)
            if cached is not None:
                if tracer is not None:
                    tracer.record_mcp()
                    tracer.record_deduped()
                    await tracer.decision("mcp.cache_hit", tool=tool, purpose=purpose)
                return MCPResult(
                    tool=tool,
                    data=cached,
                    cached=True,
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                )

        attempts = {"n": 0}

        async def _once() -> Any:
            attempts["n"] += 1
            try:
                return await asyncio.wait_for(
                    adapter.invoke(tool, normalized), timeout=spec.timeout_sec
                )
            except asyncio.TimeoutError as exc:
                raise TransientError(
                    f"MCP 工具 {tool} 超时（>{spec.timeout_sec}s）",
                    detail={"tool": tool, "timeout_sec": spec.timeout_sec},
                ) from exc
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001 - 归一化，交给重试器分类
                raise to_app_error(exc, fallback=f"MCP 工具 {tool} 调用失败") from exc

        async def _guarded() -> Any:
            return await call_with_retry(
                _once,
                policy=self._policy,
                label=f"mcp:{tool}",
                on_attempt=_attempt_hook(tracer, tool, attempts, spec),
            )

        tracer_ctx = (
            tracer.span("mcp." + tool, "mcp", **span_kwargs)
            if tracer is not None
            else _null_span()
        )
        async with tracer_ctx as box:
            try:
                if spec.idempotent:
                    # cache_ttl=0 表示「只合并并发、不缓存结果」：写后立刻读必须看到新数据
                    outcome = await self._sf.run(cache_key, _guarded, ttl=spec.cache_ttl)
                    data = outcome.value
                    deduped = outcome.deduped
                    if deduped and tracer is not None:
                        tracer.record_deduped()
                else:
                    data = await _guarded()
                    deduped = False

                data = _validate_output(spec, data)
                if spec.cache_ttl > 0 and not deduped:
                    await self._write_cache(cache_key, data, spec.cache_ttl)
                box.update(
                    {
                        "cached": False,
                        "deduped": deduped,
                        "attempts": attempts["n"],
                        "result_preview": data,
                    }
                )
                if tracer is not None:
                    tracer.record_mcp()
                return MCPResult(
                    tool=tool,
                    data=data,
                    deduped=deduped,
                    attempts=attempts["n"],
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            except AppError as exc:
                box.update({"error_kind": exc.kind.value, "attempts": attempts["n"]})
                if tracer is not None:
                    tracer.record_mcp()
                    await tracer.decision(
                        "mcp.failed",
                        tool=tool,
                        kind=exc.kind.value,
                        message=exc.message,
                        attempts=attempts["n"],
                    )
                raise

    # ---------------- 缓存辅助 ----------------

    async def _read_cache(self, key: str, tool: str) -> Any | None:
        try:
            raw = await self._cache.get(key)
        except Exception as exc:  # noqa: BLE001 - 缓存故障不能拖垮主流程
            logger.warning("[mcp] 读缓存失败 tool=%s err=%s", tool, exc)
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload.get("value") if isinstance(payload, dict) and payload.get("ok") else None

    async def _write_cache(self, key: str, data: Any, ttl: int) -> None:
        try:
            encoded = json.dumps({"ok": True, "value": data}, ensure_ascii=False)
            await self._cache.set(key, encoded, ttl=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mcp] 写缓存失败 err=%s", exc)

    async def health(self) -> dict[str, Any]:
        return await self.registry.health()


def _attempt_hook(tracer: Tracer | None, tool: str, counter: dict[str, int], spec):
    async def hook(attempt: int, exc: BaseException | None, delay: float) -> None:
        if exc is None or tracer is None:
            return
        tracer.summary.retries += 1
        await tracer.decision(
            "mcp.retry",
            tool=tool,
            attempt=attempt,
            kind=classify(exc).value,
            delay_sec=round(delay, 2),
            timeout_sec=spec.timeout_sec,
        )

    return hook


def _validate_output(spec, data: Any) -> Any:
    if spec.output_model is None:
        return data
    try:
        model = spec.output_model.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise TransientError(
            f"MCP 工具 {spec.name} 返回结构不符合契约：{_short_errors(exc)}",
            detail={"tool": spec.name, "errors": _validation_errors(exc)},
        ) from exc
    return model.model_dump()


def _validation_errors(exc: BaseException) -> list[dict[str, Any]]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return [
                {"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")}
                for e in errors()
            ]
        except Exception:  # noqa: BLE001
            return []
    return []


def _short_errors(exc: BaseException) -> str:
    items = _validation_errors(exc)
    if not items:
        return str(exc)[:200]
    return "; ".join(f"{i['loc']}: {i['msg']}" for i in items[:4])


class _null_span:
    """tracer 为空时的空上下文（保持代码路径一致，避免到处 if）。"""

    async def __aenter__(self):
        return {}

    async def __aexit__(self, *exc_info):
        return False
