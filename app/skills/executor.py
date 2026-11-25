"""Skill 执行器：权限 → 解析 → 入参校验 → 合并 → 重试 → 出参校验 → 审计。

一个 Skill 调用要过的 8 道关
---------------------------
1. 白名单：`agent.allowed_skills` 不含 → 直接拒绝（不发日志到外部系统，不消耗 token）；
2. 注册中心：Skill 不存在 / 已下线 → 结构化错误（LLM 可以看到并改主意）；
3. 入参 Schema：pydantic 校验，不合法**当场拦截**，绝不带脏参数进业务或外部系统；
4. SingleFlight：同参并发只真正执行一次（跨进程有效）；
5. 重试：只对 TRANSIENT / RATE_LIMIT 重试，次数由 Skill 自己声明（默认 1 次即不重试）；
6. 超时：每 Skill 独立超时，避免一个慢依赖拖死整条任务；
7. 出参 Schema：业务返回值也要过模型，防止字段漂移把错误数据喂给下游节点；
8. 审计：一次 span 记录 args/耗时/是否合并/重试次数/失败分类。

约定：`run()` **从不抛异常**，失败也是 SkillOutcome，由节点决定是重规划、降级还是转人工。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import ValidationError

from app.core.errors import (
    AppError,
    BusinessRuleViolation,
    PermissionDenied,
    ValidationFailed,
    classify,
    to_app_error,
)
from app.core.retry import RetryPolicy, call_with_retry
from app.core.singleflight import SingleFlight, flight_key
from app.skills.base import SkillContext
from app.skills.models import SkillCall, SkillOutcome
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillExecutor:
    def __init__(
        self,
        registry: SkillRegistry,
        *,
        singleflight: SingleFlight,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._sf = singleflight
        self._policy = policy or RetryPolicy()

    async def run(self, call: SkillCall, *, sctx: SkillContext) -> SkillOutcome:
        agent = sctx.agent
        tracer = sctx.tracer
        outcome = SkillOutcome(skill=call.skill)
        started = time.perf_counter()

        # ---- 1) 白名单 ----
        if not agent.can_use_skill(call.skill):
            outcome.fail(
                "permission",
                f"Agent {agent.id} 无权调用 {call.skill}（白名单：{agent.allowed_skills}）",
                "这个需求超出我当前能做的范围了",
            )
            tracer.record_skill(False)
            await tracer.decision(
                "skill.denied", skill=call.skill, agent=agent.id, reason="not_in_whitelist"
            )
            return outcome

        # ---- 2) 解析（是否存在 / 是否下线 / 代码能否加载） ----
        try:
            skill = await self._registry.resolve(call.skill)
        except AppError as exc:
            outcome.fail(exc.kind.value, exc.message, exc.user_message)
            tracer.record_skill(False)
            await tracer.decision(
                "skill.resolve_failed", skill=call.skill, kind=exc.kind.value, message=exc.message
            )
            return outcome

        # ---- 3) 入参 Schema ----
        try:
            payload = skill.Input.model_validate(call.args or {})
        except ValidationError as exc:
            errors = [
                {"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")}
                for e in exc.errors()
            ]
            outcome.fail(
                "validation",
                f"{call.skill} 入参不合法：{errors}",
                "我理解到的条件有歧义，这次先不猜，参数已经拦下来了",
            )
            outcome.data = {"errors": errors}
            tracer.record_skill(False)
            await tracer.decision(
                "skill.invalid_args", skill=call.skill, errors=errors, raw_args=call.args
            )
            return outcome

        normalized = payload.model_dump()
        key = flight_key(f"skill:{call.skill}", {"version": skill.version, "args": normalized})
        attempts = {"n": 0}
        policy = RetryPolicy(
            max_attempts=max(1, int(skill.retry_attempts)),
            base_delay=self._policy.base_delay,
            max_delay=self._policy.max_delay,
            jitter=self._policy.jitter,
        )

        async def _invoke() -> Any:
            attempts["n"] += 1
            try:
                return await asyncio.wait_for(skill.run(payload, sctx), timeout=skill.timeout_sec)
            except asyncio.TimeoutError as exc:
                from app.core.errors import TransientError

                raise TransientError(f"{call.skill} 执行超时（>{skill.timeout_sec}s）") from exc

        async def _guarded() -> Any:
            return await call_with_retry(
                _invoke,
                policy=policy,
                label=f"skill:{call.skill}",
                on_attempt=_attempt_hook(sctx, call.skill, attempts),
            )

        # ---- 4~7) 合并 / 重试 / 出参校验 ----
        async with tracer.span(
            f"skill.{call.skill}",
            "skill",
            agent=agent.id,
            version=skill.version,
            args=normalized,
        ) as box:
            try:
                if skill.idempotent:
                    flight = await self._sf.run(key, _guarded, ttl=skill.cache_ttl)
                    data = flight.value
                    outcome.deduped = flight.deduped
                    if flight.deduped:
                        tracer.record_deduped()
                else:
                    data = await _guarded()

                if skill.Output is not None:
                    data = _validate_output(skill, data)
                elif hasattr(data, "model_dump"):
                    data = data.model_dump()

                outcome.ok = True
                outcome.data = data if isinstance(data, dict) else {"value": data}
                outcome.attempts = attempts["n"]
                outcome.latency_ms = round((time.perf_counter() - started) * 1000, 1)
                box.update(
                    {
                        "ok": True,
                        "attempts": attempts["n"],
                        "deduped": outcome.deduped,
                        "output": outcome.data,
                    }
                )
                tracer.record_skill(True)
                return outcome
            except AppError as exc:
                outcome.fail(exc.kind.value, exc.message, exc.user_message)
                box.update({"ok": False, "error_kind": exc.kind.value, "attempts": attempts["n"]})
            except Exception as exc:  # noqa: BLE001
                wrapped = to_app_error(exc, fallback=f"{call.skill} 执行失败")
                outcome.fail(wrapped.kind.value, wrapped.message, wrapped.user_message)
                box.update({"ok": False, "error_kind": wrapped.kind.value})
                logger.exception("[skill] %s 未预期异常", call.skill)

            outcome.attempts = attempts["n"]
            outcome.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            tracer.record_skill(False)
            await tracer.decision(
                "skill.failed",
                skill=call.skill,
                kind=outcome.error_kind,
                message=outcome.message,
                attempts=attempts["n"],
            )
            return outcome

    async def run_many(
        self,
        calls: list[SkillCall],
        *,
        sctx: SkillContext,
        max_calls: int = 4,
        stop_on_permission: bool = True,
    ) -> list[SkillOutcome]:
        """顺序执行一轮内的多个调用（保持确定性，便于回放与排查）。"""
        outcomes: list[SkillOutcome] = []
        seen: set[tuple[str, str]] = set()
        for call in calls[:max_calls]:
            fingerprint = (call.skill, str(sorted(call.args.items())))
            if fingerprint in seen:
                # 模型把同一个调用写了两遍：直接跳过，避免重复计费
                await sctx.tracer.decision("skill.duplicate_in_round", skill=call.skill)
                continue
            seen.add(fingerprint)
            outcome = await self.run(call, sctx=sctx)
            outcomes.append(outcome)
            if outcome.error_kind == "permission" and stop_on_permission:
                # 越权是安全信号，不再继续执行本轮剩余调用
                break
        return outcomes


def _validate_output(skill, data: Any) -> dict[str, Any]:
    assert skill.Output is not None
    try:
        return skill.Output.model_validate(data).model_dump()
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        raise BusinessRuleViolation(
            f"{skill.name} 输出不符合契约：{errors}", detail={"errors": errors}
        ) from exc


def _attempt_hook(sctx: SkillContext, skill: str, counter: dict[str, int]):
    async def hook(attempt: int, exc: BaseException | None, delay: float) -> None:
        if exc is None or sctx.tracer is None:
            return
        sctx.tracer.summary.retries += 1
        await sctx.tracer.decision(
            "skill.retry",
            skill=skill,
            attempt=attempt,
            kind=classify(exc).value,
            delay_sec=round(delay, 2),
        )

    return hook


__all__ = ["SkillExecutor", "PermissionDenied"]
