"""参数校验 / 错误分类重试 / SingleFlight 去重（与领域无关的可靠性能力）。"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.core.errors import TransientError, ValidationFailed
from app.core.retry import RetryPolicy
from app.mcp.models import MCPAdapter, ToolSpec
from app.skills.models import SkillCall
from tests.conftest import make_ctx


class _PingInput(BaseModel):
    key: str = "x"


class CountingAdapter(MCPAdapter):
    name = "counting"
    version = "1.0.0"

    def __init__(self, fail_times: int = 0, error: BaseException | None = None) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.error = error or TransientError("upstream 503")

    async def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="counting.ping",
                adapter=self.name,
                description="计数探针",
                input_model=_PingInput,
                timeout_sec=2.0,
                cache_ttl=0,
                idempotent=True,
            )
        ]

    async def invoke(self, tool, args):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        await asyncio.sleep(0.02)
        return {"pong": args.get("key"), "seq": self.calls}


async def _ctx_with_adapter(adapter: CountingAdapter, **overrides):
    ctx = await make_ctx(**overrides)
    ctx.mcp_registry.register(adapter)
    await ctx.mcp_registry.refresh()
    return ctx


async def test_invalid_skill_args_are_blocked_before_execution():
    ctx = await make_ctx()
    sctx = ctx.skill_context("t-val", "u1", _state_agent())
    outcome = await ctx.executor.run(
        SkillCall(skill="relationship.update", args={"score": "不是数字"}), sctx=sctx
    )
    assert outcome.ok is False
    assert outcome.error_kind == "validation"
    assert not [e for e in ctx.tracer_for("t-val").tail(20) if e.get("kind") == "mcp"]


async def test_mcp_arg_validation_blocks_bad_payload():
    adapter = CountingAdapter()
    ctx = await _ctx_with_adapter(adapter)
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        await ctx.mcp.call("counting.ping", {"key": 12345})
    assert exc.value.kind.value == "validation"
    assert adapter.calls == 0


async def test_transient_error_is_retried_then_succeeds():
    adapter = CountingAdapter(fail_times=2)
    ctx = await _ctx_with_adapter(adapter)
    result = await ctx.mcp.call("counting.ping", {"key": "a"})
    assert result.data["pong"] == "a"
    assert adapter.calls == 3
    assert result.attempts == 3


async def test_validation_error_is_not_retried():
    adapter = CountingAdapter(fail_times=5, error=ValidationFailed("bad request"))
    ctx = await _ctx_with_adapter(adapter)
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        await ctx.mcp.call("counting.ping", {"key": "a"})
    assert exc.value.kind.value == "validation"
    assert adapter.calls == 1


async def test_retry_is_bounded():
    adapter = CountingAdapter(fail_times=99)
    ctx = await _ctx_with_adapter(adapter, retry_max_attempts=2)
    from app.core.errors import AppError

    with pytest.raises(AppError):
        await ctx.mcp.call("counting.ping", {"key": "a"})
    assert adapter.calls == 2


def test_retry_policy_backoff_is_bounded():
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=3.0, jitter=0.0)
    delays = [policy.delay_for(n) for n in range(1, 6)]
    assert delays == sorted(delays)
    assert max(delays) <= 3.0


async def test_same_args_are_coalesced():
    adapter = CountingAdapter()
    ctx = await _ctx_with_adapter(adapter)
    results = await asyncio.gather(*[ctx.mcp.call("counting.ping", {"key": "same"}) for _ in range(8)])
    assert adapter.calls == 1, f"8 个并发同参只应打 1 次，实际 {adapter.calls}"
    assert len({r.data["seq"] for r in results}) == 1
    assert sum(1 for r in results if r.deduped) >= 7


async def test_different_args_are_not_coalesced():
    adapter = CountingAdapter()
    ctx = await _ctx_with_adapter(adapter)
    await asyncio.gather(
        ctx.mcp.call("counting.ping", {"key": "a"}),
        ctx.mcp.call("counting.ping", {"key": "b"}),
    )
    assert adapter.calls == 2


async def test_failure_is_not_cached_for_followers():
    adapter = CountingAdapter(fail_times=99)
    ctx = await _ctx_with_adapter(adapter, retry_max_attempts=1)
    from app.core.errors import AppError

    results = await asyncio.gather(
        *[ctx.mcp.call("counting.ping", {"key": "boom"}) for _ in range(3)],
        return_exceptions=True,
    )
    assert all(isinstance(r, AppError) for r in results)
    with pytest.raises(AppError):
        await ctx.mcp.call("counting.ping", {"key": "boom"})


def _state_agent():
    from app.agents.registry import STATE_AGENT

    return STATE_AGENT
