"""统一错误模型与分类：把「可重试」和「不可重试」从异常类型里抽出来。

设计目的
--------
重试策略不能散落在各处 `try/except` 里，否则会出现：
- 参数非法也重试 3 次（浪费 token、掩盖 bug）；
- 429 不等待直接重试（打爆上游，被限流封禁）；
- 未知异常被当作可重试，长任务无限循环。

所以：所有子系统的异常都在**边界处**转成 AppError，带 kind；
重试器只看 kind，不看异常类。新增外部依赖时只需补 classify 映射。
"""
from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any


class ErrorKind(str, Enum):
    """错误的语义分类。决策依据只有这一层，避免重试逻辑与库版本耦合。"""

    TRANSIENT = "transient"        # 网络抖动、连接中断、5xx、超时 —— 可重试
    RATE_LIMIT = "rate_limit"      # 429 / 配额 —— 可重试，但必须退避
    VALIDATION = "validation"      # 参数非法、Schema 不匹配 —— 不可重试，直接拦截
    PERMISSION = "permission"      # 越权 / 未授权 —— 不可重试，必须审计
    BUSINESS = "business"          # 业务规则拒绝（如过敏食材）—— 不可重试，给用户可读原因
    NOT_FOUND = "not_found"        # 资源不存在 —— 不可重试
    INTERNAL = "internal"          # 代码缺陷 / 未知 —— 不重试，告警
    HUMAN = "human"                # 需要人工介入 —— 终止自动流程


#: 只有这两类允许自动重试。其余一律 fail fast，交给上层给人话术或人工。
RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset({ErrorKind.TRANSIENT, ErrorKind.RATE_LIMIT})


class AppError(Exception):
    """所有领域异常的基类：携带 kind / 用户可读话术 / 是否可重试。"""

    kind: ErrorKind = ErrorKind.INTERNAL
    user_message: str = "这一步没处理好，请稍后重试"

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "user_message": self.user_message,
            "retryable": self.retryable,
            "detail": self.detail,
        }


class TransientError(AppError):
    kind = ErrorKind.TRANSIENT
    user_message = "外部服务暂时不稳定，正在自动重试"


class RateLimitedError(TransientError):
    kind = ErrorKind.RATE_LIMIT
    user_message = "上游限流了，稍后自动重试"


class ValidationFailed(AppError):
    kind = ErrorKind.VALIDATION
    user_message = "参数不合法，已拦截"


class PermissionDenied(AppError):
    kind = ErrorKind.PERMISSION
    user_message = "当前 Agent 无权调用该能力"


class BusinessRuleViolation(AppError):
    kind = ErrorKind.BUSINESS
    user_message = "不符合业务规则"


class NotFoundError(AppError):
    kind = ErrorKind.NOT_FOUND
    user_message = "找不到对应数据"


class HumanRequired(AppError):
    """需要人工介入：不重试，图会切到 human_gate 节点。"""

    kind = ErrorKind.HUMAN
    user_message = "这一步需要人工确认"


def classify(exc: BaseException) -> ErrorKind:
    """把任意异常归类。**只在这里**做库相关的判断，避免污染重试逻辑。"""
    if isinstance(exc, AppError):
        return exc.kind
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorKind.TRANSIENT
    if isinstance(exc, (ConnectionError, OSError)):
        return ErrorKind.TRANSIENT

    # pydantic：参数 / Schema 问题
    try:
        from pydantic import ValidationError as PydanticValidationError

        if isinstance(exc, PydanticValidationError):
            return ErrorKind.VALIDATION
    except Exception:  # pragma: no cover - pydantic 必装，兜底而已
        pass
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        # 注意：ValueError 也可能是上游返回体解析失败，属于临时性；
        # 这里按「参数非法」处理，因为解析失败会被适配器包成 TransientError。
        return ErrorKind.VALIDATION

    name = type(exc).__name__
    module = type(exc).__module__ or ""

    # httpx
    if module.startswith("httpx"):
        if name in {"TimeoutException", "ConnectError", "ReadError", "RemoteProtocolError"}:
            return ErrorKind.TRANSIENT
        if name == "HTTPStatusError":
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status == 429:
                return ErrorKind.RATE_LIMIT
            if status >= 500:
                return ErrorKind.TRANSIENT
            return ErrorKind.BUSINESS

    # openai SDK
    if module.startswith("openai"):
        if name in {"APITimeoutError", "APIConnectionError", "InternalServerError"}:
            return ErrorKind.TRANSIENT
        if name == "RateLimitError":
            return ErrorKind.RATE_LIMIT
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return ErrorKind.PERMISSION
        if name in {"BadRequestError", "UnprocessableEntityError"}:
            return ErrorKind.VALIDATION

    # redis
    if module.startswith("redis") and name in {"ConnectionError", "TimeoutError", "BusyLoadingError"}:
        return ErrorKind.TRANSIENT

    return ErrorKind.INTERNAL


def to_app_error(exc: BaseException, *, fallback: str = "内部错误") -> AppError:
    """把任意异常包成 AppError（保留原始异常字符串便于排查）。"""
    if isinstance(exc, AppError):
        return exc
    kind = classify(exc)
    mapping = {
        ErrorKind.TRANSIENT: TransientError,
        ErrorKind.RATE_LIMIT: RateLimitedError,
        ErrorKind.VALIDATION: ValidationFailed,
        ErrorKind.PERMISSION: PermissionDenied,
        ErrorKind.BUSINESS: BusinessRuleViolation,
        ErrorKind.NOT_FOUND: NotFoundError,
        ErrorKind.HUMAN: HumanRequired,
    }
    cls = mapping.get(kind, AppError)
    return cls(f"{fallback}: {type(exc).__name__}: {exc}")


def brief_error(exc: BaseException, limit: int = 300) -> dict[str, Any]:
    """审计用：错误的结构化摘要（不含堆栈，堆栈进日志）。"""
    kind = classify(exc)
    text = str(exc)
    if len(text) > limit:
        text = text[:limit] + "…"
    return {"kind": kind.value, "type": type(exc).__name__, "message": text}


def canonical_json(value: Any) -> str:
    """稳定序列化：用于 SingleFlight / 缓存 key，保证同参不同键序命中同一个 key。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
