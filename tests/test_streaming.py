"""流式（SSE）机制测试：自定义 token 流 + 最终 state。"""
from __future__ import annotations

from app.services.session_manager import SessionManager
from tests.conftest import make_ctx


def _collect(chunk):
    return chunk if isinstance(chunk, (list, tuple)) else [chunk]


async def test_graph_stream_emits_tokens_and_final_state():
    ctx, _ = make_ctx(reply="你好呀，我是小柔，今天过得怎么样？")
    manager = SessionManager(ctx)

    state = await manager.build_state("s-stream", "在吗", "u1")
    tokens: list[str] = []
    final_state = None
    async for mode, chunk in manager.graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            for item in _collect(chunk):
                if isinstance(item, dict) and item.get("type") == "token":
                    tokens.append(item["content"])
        elif mode == "values":
            final_state = chunk

    assert "".join(tokens) == "你好呀，我是小柔，今天过得怎么样？"
    assert final_state is not None
    # astream 的 values 模式返回 dict
    assert final_state["agent_reply"] == "你好呀，我是小柔，今天过得怎么样？"

    # 流式结束后持久化已完成（persist 内联）
    history = await ctx.repo.get_recent_chat("s-stream", 10)
    assert [m.role for m in history] == ["user", "assistant"]
