"""全链路 Trace 与决策日志：每一步 LLM / Skill / MCP / 节点都留痕。

为什么这样设计
--------------
1. **落库而不是只打日志**：日志会被轮转、按采样丢弃，事后排查幻觉/参数错误要靠结构化记录；
2. **双份**：Span 走「事件流」（task_events 表，逐条落，崩溃也能留下已完成部分），
   同时内存里留一份摘要，便于任务结束时一次性回填 result；
3. **截断但不丢元信息**：prompt/response 只存前 N 字符，但保留长度、hash、token 数、
   模型名、耗时、错误分类 —— 复现问题够用，存储不会爆；
4. **Trace 绝不影响主流程**：sink 失败只打 warning，业务继续。

字段口径（审计要求）
--------------------
- 每个 span：name / kind / parent / start / duration_ms / status / attrs / error
- 每个 LLM：model、temperature、prompt_preview、response_preview、tokens_in/out、attempt
- 每个 Skill：入参（校验后）、出参摘要、deduped、attempts、是否越权
- 每个 MCP：adapter、tool、args、latency、是否命中缓存
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def truncate(value: Any, limit: int = 400) -> Any:
    """把任意值压成可入库的短预览（保留结构，剔除超长文本）。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit})"


def digest(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class TraceSummary:
    spans: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    skill_calls: int = 0
    skill_failures: int = 0
    mcp_calls: int = 0
    deduped: int = 0
    retries: int = 0
    dropped_spans: int = 0
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "spans": self.spans,
            "llm_calls": self.llm_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "skill_calls": self.skill_calls,
            "skill_failures": self.skill_failures,
            "mcp_calls": self.mcp_calls,
            "deduped": self.deduped,
            "retries": self.retries,
            "dropped_spans": self.dropped_spans,
            "errors": self.errors[-20:],
        }


class Tracer:
    """一个任务一个实例；thread-safe 不是目标（单任务单协程链路）。"""

    def __init__(
        self,
        task_id: str,
        sink: EventSink | None = None,
        *,
        enabled: bool = True,
        preview_chars: int = 400,
        max_spans: int = 500,
    ) -> None:
        self.task_id = task_id
        self._sink = sink
        self._enabled = enabled
        self._preview = preview_chars
        self._max_spans = max_spans
        self.summary = TraceSummary()
        self._seq = 0
        self._buffer: list[dict] = []

    # ---------------- 底层写入 ----------------

    async def emit(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        event = {"task_id": self.task_id, "ts": _now_iso(), **event}
        self._buffer.append(event)
        if len(self._buffer) > self._max_spans:
            self._buffer.pop(0)
            self.summary.dropped_spans += 1
        if self._sink is None:
            return
        try:
            await self._sink(event)
        except Exception as exc:  # noqa: BLE001 - Trace 失败不能影响业务
            logger.warning("[trace] sink 写入失败 task=%s err=%s", self.task_id, exc)

    # ---------------- Span ----------------

    @asynccontextmanager
    async def span(self, name: str, kind: str, **attrs: Any):
        """上下文管理器：进出自动记耗时与状态；异常记录分类后原样抛出。"""
        if not self._enabled:
            yield {}
            return

        self._seq += 1
        span_id = f"{self.task_id[:8]}-{self._seq:04d}"
        started = time.perf_counter()
        box: dict[str, Any] = dict(attrs)
        status = "ok"
        error: dict | None = None
        try:
            yield box
        except BaseException as exc:  # noqa: BLE001
            status = "error"
            from app.core.errors import brief_error

            error = brief_error(exc)
            self.summary.errors.append({"span": name, **error})
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            payload = {
                "type": "span",
                "span_id": span_id,
                "name": name,
                "kind": kind,
                "status": status,
                "duration_ms": duration_ms,
                "attrs": {k: truncate(v, self._preview) for k, v in box.items()},
            }
            if error:
                payload["error"] = error
            self.summary.spans += 1
            await self.emit(payload)

    # ---------------- 决策日志 ----------------

    async def decision(self, action: str, **payload: Any) -> None:
        """决策日志：记录「为什么这么走」，用于回溯 Agent 的思考链路。"""
        await self.emit(
            {
                "type": "decision",
                "action": action,
                "payload": {k: truncate(v, self._preview) for k, v in payload.items()},
            }
        )

    # ---------------- LLM 记账 ----------------

    def record_llm(self, usage: dict | None, *, model: str = "", attempt: int = 1) -> None:
        usage = usage or {}
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        self.summary.llm_calls += 1
        self.summary.tokens_in += tin
        self.summary.tokens_out += tout
        self.summary.retries += max(0, attempt - 1)

    def record_deduped(self, n: int = 1) -> None:
        self.summary.deduped += n

    def record_skill(self, ok: bool) -> None:
        self.summary.skill_calls += 1
        if not ok:
            self.summary.skill_failures += 1

    def record_mcp(self) -> None:
        self.summary.mcp_calls += 1

    # ---------------- 汇总 ----------------

    def tail(self, limit: int = 50) -> list[dict]:
        return self._buffer[-limit:]

    def summary_dict(self) -> dict:
        return self.summary.to_dict()


def make_tracer(task_id: str, sink: EventSink | None, settings) -> Tracer:
    return Tracer(
        task_id,
        sink,
        enabled=bool(settings.trace_enabled),
        preview_chars=int(settings.trace_preview_chars),
        max_spans=int(settings.trace_max_spans),
    )
