"""上下文裁剪 + 防死循环护栏。"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from app.core.context_window import ContextBuilder, ContextPolicy, estimate_tokens, summarize_history
from app.core.loopguard import (
    GuardLimits,
    check_budget,
    new_guard,
    observe_progress,
    observe_signature,
)
from app.graph.builder import build_graph
from app.services.chat_service import ChatService
from tests.conftest import make_ctx
from tests.stub_llm import StubLLM


def test_estimate_tokens_cjk_heavier():
    assert estimate_tokens("你好世界") > estimate_tokens("hello world")
    assert estimate_tokens("") == 0


def test_context_builder_drops_old_history_but_keeps_context():
    builder = ContextBuilder(ContextPolicy(max_tokens=700, keep_recent=2, memory_limit=2))
    history = [{"role": "user", "content": f"第{i}轮：今天想多聊一会儿，别急着睡"} for i in range(20)]
    messages, stats = builder.assemble(
        system="你是小柔",
        constraints={"stage": "暧昧", "score": 42, "mood": "shy"},
        user_input="那我们再聊五分钟",
        memories=["他喜欢猫", "他熬夜"],
        summary="之前聊过工作压力",
        history=history,
    )
    assert stats["history_dropped"] > 0
    assert stats["est_tokens"] <= 700
    body = messages[1]["content"]
    assert "stage" in body and "再聊五分钟" in body
    assert len(body) < sum(len(h["content"]) for h in history)


async def test_summarize_history_fallback_is_deterministic():
    text = await summarize_history(
        [{"role": "user", "content": "我下周要出差"}, {"role": "assistant", "content": "我记着了"}],
        max_chars=80,
    )
    assert "出差" in text


def test_signature_guard_trips_on_repeats():
    limits = GuardLimits(repeat_limit=2)
    guard = new_guard()
    payload = {"skill": "meme.reply", "args": {"mood": "sad"}}
    for _ in range(2):
        guard, reason = observe_signature(guard, limits, payload)
        assert reason is None
    guard, reason = observe_signature(guard, limits, payload)
    assert reason and reason.startswith("repeated_signature")


def test_budget_guard_trips_on_rounds_and_time():
    limits = GuardLimits(max_rounds=2, max_wall_seconds=1)
    _, reason = check_budget(new_guard(), limits, round_no=3, skill_calls=0, llm_calls=0)
    assert reason and "max_rounds" in reason

    guard = new_guard()
    guard["started_at"] -= 10
    _, reason = check_budget(guard, limits, round_no=1, skill_calls=0, llm_calls=0)
    assert reason and "max_wall_seconds" in reason


def test_no_progress_guard_trips():
    limits = GuardLimits(no_progress_limit=2)
    guard = new_guard()
    for _ in range(3):
        guard, reason = observe_progress(guard, limits, "revise|1900|2")
    assert reason and reason.startswith("no_progress")


async def test_graph_drops_repeated_tool_calls():
    """重复签名护栏：同一批工具调用第二次出现就丢弃并告警。"""
    llm = StubLLM(
        draft="好呀。",
        tool_calls=[{"skill": "meme.reply", "args": {"mood": "happy", "stage": "stranger"}}],
    )
    ctx = await make_ctx(llm, graph_repeat_limit=0)
    graph = build_graph(ctx, InMemorySaver())
    service = ChatService(ctx, graph, {"backend": "memory"})
    session = await service.create_session(None, "u1")
    payload = await service.chat(
        session_id=session.session_id, user_id="u1", user_input="发个表情"
    )
    assert payload["status"] == "done"
    assert any("重复工具调用" in w for w in payload["warnings"])
