"""幂等键：同一消息重试不重复落库。"""
from __future__ import annotations

from app.services.session_manager import SessionManager
from tests.conftest import make_ctx


async def test_idempotent_retry_returns_cached_result_and_no_dup_write():
    ctx, _ = make_ctx(reply="嗯哼，我在呢～你今天过得怎么样？")
    manager = SessionManager(ctx)

    r1 = await manager.chat("s-idem", "在吗", "u1", client_msg_id="m1")
    assert r1["reply"]
    assert len(await ctx.repo.get_recent_chat("s-idem", 10)) == 2
    score1 = r1["relationship_score"]

    # 同一幂等键重试：返回缓存结果，不重复落库、不重复推进关系
    r2 = await manager.chat("s-idem", "在吗", "u1", client_msg_id="m1")
    assert r2 == r1
    assert len(await ctx.repo.get_recent_chat("s-idem", 10)) == 2
    snap = await ctx.repo.get_session("s-idem")
    assert snap.relationship_score == score1

    # 新幂等键：正常新增一轮
    await manager.chat("s-idem", "还在吗", "u1", client_msg_id="m2")
    assert len(await ctx.repo.get_recent_chat("s-idem", 10)) == 4


async def test_persist_claim_prevents_double_write():
    """结果缓存丢失、但落库标记仍在时，重试不再重复落库。"""
    ctx, _ = make_ctx(reply="嗯哼，我在呢～")
    manager = SessionManager(ctx)

    # 模拟上一次已落库、但结果缓存未写入（例如落库后进程崩溃）的场景
    await ctx.cache.set_nx("idem:s-claim:m1:written", "1", ttl=3600)

    result = await manager.chat("s-claim", "在吗", "u1", client_msg_id="m1")
    # 回复仍会生成，但 persist 被抢占标记短路，不再写聊天历史
    assert result["reply"]
    assert await ctx.repo.get_recent_chat("s-claim", 10) == []
