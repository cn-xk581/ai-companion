"""本地演示：和她聊两轮，看回复、关系/情绪变化与审计摘要。

用法：
    python -m examples.companion_demo --offline     # 不调真实模型（StubLLM）
    python -m examples.companion_demo               # 需 .env 配好 LLM_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile

from app.config import get_settings
from app.core.checkpointing import open_checkpointer
from app.core.retry import policy_from_settings
from app.core.singleflight import SingleFlight
from app.graph.builder import build_graph
from app.graph.context import GraphContext
from app.llm.clients import OpenAILLMClient, UnavailableLLM
from app.llm.embedder import create_embedder
from app.mcp.client import MCPClient
from app.mcp.registry import build_default_registry
from app.memory.factory import create_vector_store
from app.services.chat_service import ChatService
from app.skills.builtin import build_builtin_skills
from app.skills.executor import SkillExecutor
from app.skills.registry import SkillRegistry, create_skill_store
from app.storage.postgres import create_repository
from app.storage.redis_client import create_cache


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="用 StubLLM，不调真实模型")
    args = parser.parse_args()

    settings = get_settings()
    if args.offline:
        settings = settings.model_copy(
            update={
                "database_url": "",
                "redis_url": "",
                "vector_store_backend": "memory",
                "embedding_backend": "hash",
                "checkpoint_backend": "memory",
                "mcp_state_dir": tempfile.mkdtemp(prefix="companion-demo-"),
            }
        )
        from tests.stub_llm import StubLLM

        llm = StubLLM(draft="嗯，我在。今天是不是又没好好吃饭？")
    else:
        llm = (
            OpenAILLMClient(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url or None,
                model=settings.llm_model,
            )
            if settings.llm_api_key
            else UnavailableLLM()
        )

    repo = create_repository(settings.database_url)
    if hasattr(repo, "init_db"):
        await repo.init_db()
    cache = create_cache(settings.redis_url)
    embedder = create_embedder(settings)
    vector_store = create_vector_store(settings, repo, embedder.dim)
    await vector_store.init()
    mcp_registry = build_default_registry(settings)
    await mcp_registry.start_all()

    skill_store = create_skill_store(settings.redis_url)
    skills = SkillRegistry(
        skill_store, builtins=build_builtin_skills(), refresh_sec=settings.skill_refresh_sec
    )
    await skills.bootstrap()
    singleflight = SingleFlight(cache, enabled=settings.singleflight_enabled)
    policy = policy_from_settings(settings)
    ctx = GraphContext(
        llm=llm,
        embedder=embedder,
        repo=repo,
        cache=cache,
        vector_store=vector_store,
        settings=settings,
        skills=skills,
        executor=SkillExecutor(skills, singleflight=singleflight, policy=policy),
        mcp=MCPClient(mcp_registry, cache=cache, singleflight=singleflight, policy=policy),
        mcp_registry=mcp_registry,
        singleflight=singleflight,
    )

    async with open_checkpointer(settings) as checkpoint:
        graph = build_graph(ctx, checkpoint.saver)
        service = ChatService(ctx, graph, checkpoint.describe())
        session = await service.create_session(None, "demo-user")
        print(f"会话：{session.session_id}（人设：{session.persona.name}）\n")

        for text in ("今天加班到很晚，有点累", "你记得我喜欢喝什么吗"):
            payload = await service.chat(
                session_id=session.session_id, user_id="demo-user", user_input=text
            )
            print(f"我：{text}")
            print(f"她：{payload['reply']}")
            print(
                f"    [阶段 {payload.get('stage_label')} · 分数 {payload.get('score')} · "
                f"情绪 {payload.get('mood_label')} · 状态 {payload['status']}]\n"
            )

        trace = await service.trace(payload["turn_id"], "demo-user")
        print(f"审计事件 {trace['count']} 条，示例：")
        for event in trace["events"][:5]:
            print("  ", json.dumps(event, ensure_ascii=False)[:150])

    await mcp_registry.close_all()
    await skill_store.close()
    await vector_store.close()
    await cache.close()
    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
