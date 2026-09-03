"""本地演示：注册/登录 + 真实模型多轮对话 + 流式输出（需要 .env 里的 LLM_API_KEY）。

用法：python -m examples.chat_demo
"""
from __future__ import annotations

import asyncio

from app.config import get_settings
from app.graph.context import GraphContext
from app.llm.clients import OpenAILLMClient
from app.llm.embedder import create_embedder
from app.memory.factory import create_vector_store
from app.services.session_manager import SessionManager
from app.storage.postgres import create_repository
from app.storage.redis_client import create_cache


async def main() -> None:
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("请在 .env 配置 LLM_API_KEY（已移除 FakeLLM，必须使用真实模型）")

    print(f"[demo] 使用真实模型：{settings.llm_model} @ {settings.llm_base_url}")
    llm = OpenAILLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        image_model=settings.image_model,
    )

    repo = create_repository(settings.database_url)
    cache = create_cache(settings.redis_url)
    embedder = create_embedder(settings)
    vector_store = create_vector_store(settings, repo, embedder.dim)
    ctx = GraphContext(llm=llm, embedder=embedder, repo=repo, cache=cache, vector_store=vector_store, settings=settings)
    manager = SessionManager(ctx)

    user_id = "demo-user"
    turns = [
        "在吗？今天有点想你",
        "我们是什么时候在一起的呀？",
        "我喜欢喝三分糖的奶茶，你要记住哦",
    ]
    for text in turns:
        result = await manager.chat("demo-session", text, user_id)
        print(f"\n[用户] {text}")
        print(f"[{result['relationship_stage']} | {result['mood']} {result['mood_intensity']:.2f}] {result['reply']}")

    # 流式演示
    print("\n===== 流式输出 =====\n[小柔] ", end="")
    state = await manager.build_state("demo-session", "那你觉得我们是什么关系呀", user_id)
    async for mode, chunk in manager.graph.astream(state, stream_mode=["custom"]):
        if mode == "custom":
            for item in (chunk if isinstance(chunk, (list, tuple)) else [chunk]):
                if isinstance(item, dict) and item.get("type") == "token":
                    print(item["content"], end="", flush=True)
    print()

    print("\n===== 记忆库内容 =====")
    for m in await manager.list_memories("demo-session", user_id):
        print(f"- [{m.category}] {m.content} (importance={m.importance})")

    await repo.close()
    await cache.close()
    await vector_store.close()


if __name__ == "__main__":
    asyncio.run(main())
