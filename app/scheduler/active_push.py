"""主动消息推送：独立后台定时任务，不跑在 LangGraph 对话链路内部。

职责：
- 定时扫描活跃 session，按概率 + 频率控制（Redis 计数）决定是否推送；
- 用小模型/低 token 生成一句自然的消息；
- 通过 ctx.notifier 回调接入层（WebSocket / Webhook / 推送服务）下发；
- 推送内容同时写入聊天历史，保证上下文连贯。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from datetime import date

from app.graph.context import GraphContext
from app.graph.state import ChatMessage
from app.llm.prompts import build_push_messages
from app.persona.models import MOOD_LABELS

logger = logging.getLogger(__name__)


async def active_push_loop(ctx: GraphContext) -> None:
    settings = ctx.settings
    logger.info("active push loop started (interval=%ss, chance=%.2f)", settings.active_push_interval_sec, settings.active_push_chance)
    while True:
        try:
            await _maybe_push_once(ctx)
        except asyncio.CancelledError:
            logger.info("active push loop stopped")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("active push loop error")
        await asyncio.sleep(settings.active_push_interval_sec)


async def _maybe_push_once(ctx: GraphContext) -> None:
    settings = ctx.settings
    session_ids = await ctx.repo.list_session_ids()
    for session_id in session_ids:
        # 1. 每日推送次数上限
        day_key = f"push:{session_id}:{date.today().isoformat()}"
        today_count = int((await ctx.cache.get(day_key)) or 0)
        if today_count >= settings.active_push_max_per_session_per_day:
            continue
        # 2. 随机概率
        if random.random() > settings.active_push_chance:
            continue
        # 3. 距上次推送间隔
        last = await ctx.cache.get(f"push:{session_id}:last")
        if last and (time.time() - float(last)) < settings.active_push_interval_sec:
            continue

        snapshot = await ctx.repo.get_session(session_id)
        if snapshot is None:
            continue
        history = await ctx.repo.get_recent_chat(session_id, 5)
        last_activity = history[-1].content if history else "（还没有聊过天）"
        memories = await ctx.repo.list_memories(session_id)
        mood_label = MOOD_LABELS.get(snapshot.mood.current_mood, snapshot.mood.current_mood)

        messages = build_push_messages(snapshot.persona, snapshot.relationship_stage, mood_label, memories, last_activity)
        try:
            result = await ctx.llm.complete(messages, temperature=0.9, max_tokens=120)
            text = (result.content or "").strip()[:120]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] push generate failed: %s", session_id, exc)
            continue
        if not text:
            continue

        # 下发 + 记录历史 + 更新频率计数
        await ctx.repo.append_chat(session_id, ChatMessage(role="assistant", content=text))
        if ctx.notifier is not None:
            ret = ctx.notifier(session_id, text)
            if inspect.isawaitable(ret):
                await ret
        await ctx.cache.incr(day_key)
        await ctx.cache.set(f"push:{session_id}:last", str(time.time()))
        logger.info("[%s] pushed: %s", session_id, text)
