"""LLM 调用封装：重试 + 结构化解析 + 全量记账（token / 耗时 / 输入输出摘要）。

为什么不在节点里直接 ctx.llm.complete
------------------------------------
1. 重试策略要统一（429/超时要退避，参数错误不重试）；
2. token 消耗必须逐次记账，否则「成本失控」无从定位；
3. 结构化输出要统一解析（去 ```json 围栏、容错提取首尾大括号）；
4. 失败要能区分「模型输出不合法」（可修复一次）和「上游不可用」（可重试/降级）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationFailed
from app.core.retry import RetryPolicy, call_with_retry
from app.core.tracing import Tracer, digest, truncate

logger = logging.getLogger(__name__)


@dataclass
class LLMJsonResult:
    data: dict[str, Any]
    usage: dict[str, Any]
    attempts: int = 1


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出里抠出 JSON 对象：容忍 ```json 围栏、前后废话、单引号。"""
    if not text:
        raise ValidationFailed("模型返回为空")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.lstrip("json").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValidationFailed(f"模型输出不是合法 JSON：{truncate(text, 160)}")
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValidationFailed(f"模型输出 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValidationFailed("模型输出的 JSON 顶层必须是对象")
    return data


async def complete_json(
    *,
    ctx,
    tracer: Tracer,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    purpose: str,
    model: str = "",
) -> LLMJsonResult:
    """调用 LLM 并解析为 JSON 对象；对 TRANSIENT/RATE_LIMIT 自动重试。"""
    attempts = {"n": 0}

    async def _once():
        attempts["n"] += 1
        return await ctx.llm.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

    async def _on_attempt(attempt: int, exc: BaseException | None, delay: float) -> None:
        if exc is not None:
            tracer.summary.retries += 1
            await tracer.decision(
                "llm.retry", purpose=purpose, attempt=attempt, delay_sec=round(delay, 2)
            )

    async with tracer.span(
        f"llm.{purpose}",
        "llm",
        model=model or getattr(ctx.llm, "model", ""),
        temperature=temperature,
        prompt_digest=digest(messages[-1]["content"]),
        prompt_preview=messages[-1]["content"],
    ) as box:
        result = await call_with_retry(
            _once,
            policy=RetryPolicy(max_attempts=3, base_delay=0.5, max_delay=6.0),
            label=f"llm:{purpose}",
            on_attempt=_on_attempt,
        )
        tracer.record_llm(result.usage, model=model, attempt=attempts["n"])
        content = result.content or ""
        box.update(
            {
                "response_preview": content,
                "tokens_in": (result.usage or {}).get("prompt_tokens"),
                "tokens_out": (result.usage or {}).get("completion_tokens"),
                "attempts": attempts["n"],
                "finish_reason": result.finish_reason,
            }
        )
        return LLMJsonResult(data=parse_json_object(content), usage=result.usage or {}, attempts=attempts["n"])


async def complete_text(
    *,
    ctx,
    tracer: Tracer,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    purpose: str,
    model: str = "",
) -> str:
    """生成自然语言（只用于最终解释，不参与结构化决策）。"""

    async def _once():
        parts: list[str] = []
        async for token in ctx.llm.stream(
            messages, temperature=temperature, max_tokens=max_tokens
        ):
            parts.append(token)
        return "".join(parts)

    async with tracer.span(
        f"llm.{purpose}",
        "llm",
        model=model or getattr(ctx.llm, "model", ""),
        temperature=temperature,
        prompt_preview=messages[-1]["content"],
    ) as box:
        text = await call_with_retry(
            _once,
            policy=RetryPolicy(max_attempts=2, base_delay=0.4, max_delay=4.0),
            label=f"llm:{purpose}",
        )
        tracer.record_llm({}, model=model)  # 流式接口没有 usage，按 0 记账并标注
        box.update({"response_preview": text, "tokens_in": None, "tokens_out": None})
        return (text or "").strip()


async def stream_tokens(
    *,
    ctx,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    attempts: int = 2,
    retry_delay: float = 0.4,
):
    """流式产出 token 的生成器：**只在还没吐出任何 token 时**重试。

    一旦已经有 token 发给前端，重试会导致重复内容，因此宁可截断也不重来；
    节点据此把「部分回复 + 中断标记」交给后续处理。
    """
    import asyncio

    from app.core.errors import classify
    from app.core.errors import RETRYABLE_KINDS

    yielded = False
    last_exc: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            async for token in ctx.llm.stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ):
                if token:
                    yielded = True
                    yield token
            return
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            retryable = classify(exc) in RETRYABLE_KINDS
            if yielded or attempt >= attempts or not retryable:
                raise
            await asyncio.sleep(retry_delay)
    if last_exc is not None:  # pragma: no cover - 防御式收尾
        raise last_exc


def stream_failure_fallback(exc: BaseException) -> str:
    """流式失败时的兜底话术：不是技术错误，而是一句符合人设的话。"""
    from app.core.errors import classify

    kind = classify(exc)
    if kind.value in ("transient", "rate_limit"):
        return "等我一下，刚才有点走神…你说到哪儿了？"
    return "唔…我刚才没接住，你再说一遍好不好。"
