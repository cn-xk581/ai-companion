"""上下文裁剪与摘要：控制长任务的 token 曲线。

问题
----
多轮 revise 之后，如果把「每轮 plan 全文 + 每轮 review 全文 + 全部历史」都塞进 prompt：
- 输入 token 线性增长，成本失控；
- 噪声压过信号，模型更容易在旧版本方案上改错。

策略（按优先级保）
------------------
1. 永不可裁：system 指令、结构化约束（目标热量/忌口过敏）、本轮用户输入；
2. 次优先：跨任务长期偏好（记忆），最多 memory_limit 条；
3. 再次：最近 keep_recent 轮对话原文（由近及远塞，塞不下就停）；
4. 被裁掉的更早历史 → 用摘要替代（摘要本身也限长，且只在需要时生成）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
    )


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文 1 字≈1 token，英文/数字≈4 字符 1 token。

    刻意不引入 tiktoken（多模型不通用、体积大）；估算用于**预算决策**，
    偏差 ±20% 可接受，真实消耗以 LLM 返回的 usage 为准（Trace 里记录）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


@dataclass(frozen=True)
class ContextPolicy:
    max_tokens: int = 3000
    keep_recent: int = 6
    memory_limit: int = 6
    summary_max_chars: int = 400
    block_max_chars: int = 1200

    @property
    def budget(self) -> int:
        return max(512, self.max_tokens)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断 {len(text) - limit} 字）"


def render_constraints(constraints: dict[str, Any], order: list[str] | None = None) -> str:
    """把结构化上下文 render 成稳定文本（顺序固定，便于缓存命中与人工核对）。

    不再绑定某个领域的关键字清单：传什么渲染什么，`order` 只控制顺序。
    """
    keys = order or list((constraints or {}).keys())
    lines: list[str] = []
    for key in keys:
        if key not in constraints or constraints[key] in (None, "", []):
            continue
        value = constraints[key]
        if isinstance(value, list):
            value = "、".join(str(v) for v in value)
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


class ContextBuilder:
    """预算驱动的消息组装器：先保必需项，再按优先级填可选块。"""

    def __init__(self, policy: ContextPolicy | None = None) -> None:
        self.policy = policy or ContextPolicy()

    def assemble(
        self,
        *,
        system: str,
        constraints: dict[str, Any],
        user_input: str,
        memories: list[str] | None = None,
        summary: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        policy = self.policy
        budget = policy.budget
        used = estimate_tokens(system) + estimate_tokens(user_input) + 40
        blocks: list[str] = []

        constraints_block = render_constraints(constraints)
        if constraints_block:
            constraints_block = _clip(constraints_block, policy.block_max_chars)
            blocks.append("【硬约束（由系统计算，禁止改写）】\n" + constraints_block)
            used += estimate_tokens(constraints_block)

        memory_lines: list[str] = []
        for mem in (memories or [])[: policy.memory_limit]:
            line = f"- {_clip(mem, 120)}"
            cost = estimate_tokens(line)
            if used + cost > budget * 0.75:
                break
            memory_lines.append(line)
            used += cost
        if memory_lines:
            blocks.append("【长期偏好（历史记忆）】\n" + "\n".join(memory_lines))

        recent_lines, dropped = self._take_recent(history or [], budget - used)
        if summary and (dropped or recent_lines):
            summary = _clip(summary, policy.summary_max_chars)
            blocks.append("【更早的进展摘要】\n" + summary)
            used += estimate_tokens(summary)

        blocks.append("【本轮输入】\n" + _clip(user_input, policy.block_max_chars))
        blocks.append(f"【预算】剩余 token 预算约 {max(0, budget - used)}")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(blocks)},
        ]
        stats = {
            "est_tokens": used,
            "budget": budget,
            "memories_used": len(memory_lines),
            "history_used": len(recent_lines),
            "history_dropped": dropped,
            "summary_used": bool(summary),
            "summary_chars": len(summary),
        }
        return messages, stats

    def _take_recent(self, history: list[dict[str, str]], remaining: int) -> tuple[list[str], int]:
        """由近及远取历史，剩余预算用完即停；返回 (行, 被丢弃条数)。"""
        lines: list[str] = []
        used = 0
        fetched = 0
        for item in reversed(history):
            fetched += 1
            if len(lines) >= self.policy.keep_recent and used > remaining * 0.4:
                break
            role = item.get("role", "user")
            label = {"user": "用户", "assistant": "我", "system": "系统"}.get(role, role)
            line = f"{label}: {_clip(item.get('content', ''), 240)}"
            cost = estimate_tokens(line)
            if used + cost > remaining * 0.5 and lines:
                break
            lines.append(line)
            used += cost
        lines.reverse()
        dropped = max(0, len(history) - len(lines))
        return lines, dropped


Summarizer = Callable[[list[dict[str, str]], int], Awaitable[str]]


async def summarize_history(
    history: list[dict[str, str]],
    *,
    max_chars: int = 400,
    llm_summarize: Summarizer | None = None,
) -> str:
    """把早期历史压成摘要。有 LLM 用 LLM，没有就做确定性折叠（保证离线可测）。"""
    if not history:
        return ""
    if llm_summarize is not None:
        try:
            return await llm_summarize(history, max_chars)
        except Exception as exc:  # noqa: BLE001 - 摘要失败必须能降级
            logger.warning("[context] LLM 摘要失败，降级为规则折叠: %s", exc)
    parts = []
    for item in history:
        role = item.get("role", "user")
        label = {"user": "用户", "assistant": "我"}.get(role, role)
        parts.append(f"{label}:{_clip(item.get('content', ''), 60)}")
    merged = " / ".join(parts)
    return _clip(merged, max_chars)
