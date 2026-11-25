"""防死循环 / 防跑飞：多维度护栏，且状态可随 Checkpoint 持久化。

为什么用「纯函数 + dict 状态」
----------------------------
护栏状态要进 LangGraph State 才能随断点续跑一起恢复；如果做成有状态对象，
进程重启后计数清零，等于没有护栏。所以这里所有函数都是
`guard(dict) -> (dict, reason|None)`，节点负责把新 dict 写回 state。

四道闸门
--------
1. 轮次上限（max_rounds）：plan→execute→review 的循环次数；
2. 总预算（skill_calls / llm_calls / wall_seconds）：长任务成本与耗时的硬上限；
3. 重复签名（repeat_limit）：同一批 skill+args 反复出现 => 模型在打转；
4. 无进展（no_progress_limit）：连续 N 轮 review 结论与关键指标完全一致 => 改不动了。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import canonical_json


@dataclass(frozen=True)
class GuardLimits:
    max_rounds: int = 3
    max_skill_calls: int = 12
    max_llm_calls: int = 8
    max_wall_seconds: float = 120.0
    repeat_limit: int = 2
    no_progress_limit: int = 2


def new_guard() -> dict[str, Any]:
    return {
        "started_at": time.time(),
        "signatures": {},       # signature -> 出现次数
        "last_progress_key": "",
        "no_progress": 0,
        "trips": [],            # 触发记录（审计用）
    }


def _signature(payload: Any) -> str:
    return hashlib.sha1(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def observe_signature(guard: dict, limits: GuardLimits, payload: Any) -> tuple[dict, str | None]:
    """记录一次「调用签名」，同一签名重复超过阈值即判定打转。"""
    guard = dict(guard or new_guard())
    signatures = dict(guard.get("signatures") or {})
    sig = _signature(payload)
    signatures[sig] = int(signatures.get(sig, 0)) + 1
    guard["signatures"] = signatures
    if signatures[sig] > limits.repeat_limit:
        reason = f"repeated_signature:{sig}:{signatures[sig]}"
        guard["trips"] = [*guard.get("trips", []), reason]
        return guard, reason
    return guard, None


def observe_progress(guard: dict, limits: GuardLimits, progress_key: str) -> tuple[dict, str | None]:
    """记录一次「进展指纹」（如 review verdict + 关键指标），连续不变即判定无进展。"""
    guard = dict(guard or new_guard())
    if progress_key and progress_key == guard.get("last_progress_key"):
        guard["no_progress"] = int(guard.get("no_progress", 0)) + 1
    else:
        guard["no_progress"] = 0
    guard["last_progress_key"] = progress_key
    if int(guard["no_progress"]) >= limits.no_progress_limit:
        reason = f"no_progress:{progress_key[:24]}"
        guard["trips"] = [*guard.get("trips", []), reason]
        return guard, reason
    return guard, None


def check_budget(
    guard: dict,
    limits: GuardLimits,
    *,
    round_no: int,
    skill_calls: int,
    llm_calls: int,
) -> tuple[dict, str | None]:
    """预算闸门：轮次 / skill 次数 / LLM 次数 / 墙钟时间，任一超限即返回原因。"""
    guard = dict(guard or new_guard())
    reasons: list[str] = []
    if round_no > limits.max_rounds:
        reasons.append(f"max_rounds:{round_no}>{limits.max_rounds}")
    if skill_calls > limits.max_skill_calls:
        reasons.append(f"max_skill_calls:{skill_calls}>{limits.max_skill_calls}")
    if llm_calls > limits.max_llm_calls:
        reasons.append(f"max_llm_calls:{llm_calls}>{limits.max_llm_calls}")
    elapsed = time.time() - float(guard.get("started_at") or time.time())
    if elapsed > limits.max_wall_seconds:
        reasons.append(f"max_wall_seconds:{elapsed:.0f}>{limits.max_wall_seconds:.0f}")
    if reasons:
        reason = ";".join(reasons)
        guard["trips"] = [*guard.get("trips", []), reason]
        return guard, reason
    return guard, None


def guard_snapshot(guard: dict) -> dict:
    """给 /trace 与结果里用的只读快照。"""
    guard = guard or {}
    return {
        "elapsed_sec": round(time.time() - float(guard.get("started_at") or time.time()), 1),
        "signatures": len(guard.get("signatures") or {}),
        "no_progress": int(guard.get("no_progress", 0)),
        "trips": list(guard.get("trips") or []),
    }
