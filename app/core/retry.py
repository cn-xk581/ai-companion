"""重试策略：只重试「可重试」错误，指数退避 + 抖动，尊重 Retry-After。

为什么要单独一层
----------------
1. 上游（LLM / MCP HTTP / Redis）抖动是常态，不能一次失败就整条任务失败；
2. 但参数非法、越权、业务拒绝重试毫无意义，只会放大延迟和成本；
3. 必须有上限：attempts、单次延迟上限、总延迟上限，禁止无限重试。

约定
----
- 重试器**只**依据 `ErrorKind` 决策（见 app/core/errors.py）；
- 每次尝试都通过 `on_attempt` 回调上报，用于 Trace 记录「第几次、为什么」；
- 最终失败统一抛 AppError（保留 kind），上层据此决定 HITL / 降级 / 报错。
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

from app.core.errors import (
    RETRYABLE_KINDS,
    AppError,
    ErrorKind,
    RateLimitedError,
    classify,
    to_app_error,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: on_attempt(attempt, exc, next_delay)：attempt 从 1 开始；成功时 exc=None
AttemptHook = Callable[[int, BaseException | None, float], Awaitable[None] | None]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.4
    max_delay: float = 8.0
    jitter: float = 0.25
    retry_kinds: frozenset[ErrorKind] = field(default=RETRYABLE_KINDS)
    honor_retry_after: bool = True

    def delay_for(self, attempt: int, exc: BaseException | None = None) -> float:
        """指数退避：base * 2^(n-1)，封顶 max_delay，再加 ±jitter 抖动防止惊群。"""
        if self.honor_retry_after and isinstance(exc, RateLimitedError) and exc.retry_after:
            return min(float(exc.retry_after), self.max_delay)
        raw = self.base_delay * (2 ** (attempt - 1))
        raw = min(raw, self.max_delay)
        if self.jitter:
            raw *= 1 + random.uniform(-self.jitter, self.jitter)
        return max(0.0, raw)


#: 无状态默认策略（LLM / MCP 调用共用）
DEFAULT_POLICY = RetryPolicy()


def policy_from_settings(settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max(1, int(settings.retry_max_attempts)),
        base_delay=float(settings.retry_base_delay),
        max_delay=float(settings.retry_max_delay),
        jitter=float(settings.retry_jitter),
    )


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    label: str = "",
    on_attempt: AttemptHook | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """执行 fn()，按策略重试。最终失败抛 AppError（保留原始 kind 与可读信息）。"""
    policy = policy or DEFAULT_POLICY
    attempts = max(1, policy.max_attempts)
    last_exc: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = await fn()
            if on_attempt is not None:
                maybe = on_attempt(attempt, None, 0.0)
                if asyncio.iscoroutine(maybe):
                    await maybe
            return result
        except asyncio.CancelledError:
            # 取消不算失败，不能重试（否则会拖住请求取消）
            raise
        except BaseException as exc:  # noqa: BLE001 - 统一分类后决定是否重试
            last_exc = exc
            kind = classify(exc)
            retryable = kind in policy.retry_kinds and attempt < attempts
            delay = policy.delay_for(attempt, exc) if retryable else 0.0
            if on_attempt is not None:
                maybe = on_attempt(attempt, exc, delay)
                if asyncio.iscoroutine(maybe):
                    await maybe
            if not retryable:
                logger.warning(
                    "[retry] %s 失败且不可重试 kind=%s attempt=%d/%d err=%s",
                    label or getattr(fn, "__name__", "call"),
                    kind.value,
                    attempt,
                    attempts,
                    exc,
                )
                raise to_app_error(exc, fallback=label or "调用失败") from exc
            logger.info(
                "[retry] %s 第 %d/%d 次失败 kind=%s，%.2fs 后重试：%s",
                label or getattr(fn, "__name__", "call"),
                attempt,
                attempts,
                kind.value,
                delay,
                exc,
            )
            await sleep(delay)

    raise to_app_error(last_exc or RuntimeError("retry exhausted"), fallback=label or "调用失败")
