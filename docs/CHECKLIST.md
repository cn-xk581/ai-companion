# 落地检查清单 · AI 伴侣 Agent

> 逐项验收，勾掉即视为该能力在环境里真的生效。手工项都给了可直接粘贴的命令。

## 0. 准备

```bash
pip install -e ".[dev]"
pytest -q                                  # 期望 55 passed
python -m examples.companion_demo --offline # 不调模型跑两轮
```

- [ ] `pytest -q` 全绿且 < 30s（没有偷偷连外部依赖）
- [ ] demo 打印两轮「她」的回复、阶段/分数/情绪变化与审计事件

## 1. 分层边界

- [ ] `grep -rn "asyncpg\|httpx" app/skills/` 无结果（Skill 不直连外部）
- [ ] `grep -rn "stranger\|crush\|intimacy" app/mcp/` 只出现在透传字段，没有风控逻辑
- [ ] `grep -rn "def _route" app/graph/` 只有路由判断，没有业务规则
- [ ] `app/mcp/adapters/calendar_local.py` 只有 CRUD/时间归一化
- [ ] `app/skills/builtin/safety.py` 里能看到全部风控规则与分级

## 2. 多 Agent 隔离

- [ ] replier 调 `reminder.create` → `permission`，trace 有 `skill.denied`
- [ ] Skill 在白名单但 Agent 缺 `calendar.*` → MCP 层仍拒绝（`tests/test_isolation.py`）
- [ ] `memory.recall` 同时被 intake/replier 调用成功（共享能力）
- [ ] 下线 `meme.reply` 后调用 → `business`「已下线」

## 3. 注册中心

```bash
redis-cli HGETALL skill:meta | head
redis-cli GET skill:rev
```

- [ ] `POST /skills/{name}/disable`（X-Admin-Token）→ 立即生效，`/skills` 显示下线
- [ ] `POST /skills/register` 注册外部 Skill → 可解析可调用；POST 的 schema 被忽略
- [ ] `DELETE /skills/{name}` → 解析报 NotFound
- [ ] 多副本在 `SKILL_REFRESH_SEC` 内看到变更；未配 ADMIN_TOKEN 时运维接口 403

## 4. 可靠性与人工复核

- [ ] `GRAPH_MAX_ROUNDS=0/重复工具调用` → 工具被丢弃并告警（`tests/test_context_and_guard.py`）
- [ ] 输入「我不想活了」→ `status=awaiting_review`，草稿含 12356 热线
- [ ] `POST /turns/{id}/resume {approve}` → done，且 `skill.safety.guard` span 数不变（不重跑）
- [ ] `reject` → 回复被替换为安全话术
- [ ] 同参并发 MCP 调用只打一次上游；失败不写缓存
- [ ] `TRANSIENT` 两次后成功 → `attempts=3`；`VALIDATION` → 1 次；一直失败 → 等于上限
- [ ] 同 `client_msg_id` 重发 → 同 turn_id，不再调模型

## 5. 可观测

- [ ] `/turns/{id}/trace` 同时有 `llm`、`skill`、（用到工具时）`mcp` 的 span
- [ ] span 有耗时/状态/入参出参摘要；失败带 `error.kind`
- [ ] 决策日志完整：`intake.done → plan.done → respond.done → turn.persisted`，复核时有 `human.*`
- [ ] 仅凭 trace 能回答：为什么被拦、用了哪个工具、哪一步慢了

## 6. 风控与幻觉

- [ ] 注入（"忽略以上指令…"）→ blocked，且 LLM 一次都没调（看 trace/计数）
- [ ] stranger 说露骨内容 → blocked（stage_mismatch），话术符合人设
- [ ] 模型产出出戏句（"作为 AI…"）→ `reply.polish` 移除并留痕
- [ ] 工具参数非法 → `validation`，不触达 MCP
- [ ] 长对话下历史被裁剪/摘要，但人设、阶段、最近输入仍在

## 7. API 与前端

- [ ] 注册 → `POST /sessions` → `/chat/stream` 能看到 `token` 逐字输出与 `done`
- [ ] 刷新页面后历史还在；会话列表显示阶段/情绪/最后一句
- [ ] 改人设 → 下一句回复按新人设（`PUT /sessions/{id}/persona`）
- [ ] 记忆页能删除单条；重置会话清空对话与记忆
- [ ] 前端双主题切换、追踪抽屉、移动端单栏均可用

## 8. 部署与回收

```bash
bash deploy/selftest.sh                                  # 27 项安全原语
sudo bash deploy/install.sh --dry-run --domain chat.example.com
sudo bash deploy/uninstall.sh --dry-run
```

- [ ] selftest 全绿；安装/卸载 dry-run 零副作用
- [ ] 安装后 `/api/v1/health` → `status=ok`、`checkpoint.backend=postgres`
- [ ] 端口只在内网：`ss -ltnp | grep -E ':(8000|5432|6379)'`
- [ ] 卸载后自检 8 项全 ✓；6 条「干净」命令均为空（见 `deploy/README.md` §6）

---

## 自动化对照

| 能力 | 测试 |
|---|---|
| 正常对话/状态落库/记忆 | `tests/test_chat_e2e.py::test_normal_turn_persists_reply_state_and_trace` |
| 注入拦截 | `…::test_prompt_injection_is_blocked_without_llm` |
| 越界拦截 | `…::test_stage_mismatch_is_blocked` |
| 自伤转人工 + 恢复 | `…::test_self_harm_turn_goes_to_human_review_and_resumes` |
| 输出复核驳回 | `…::test_output_review_reject_replaces_with_safe_reply` |
| 流式 | `…::test_streaming_emits_tokens_then_done` |
| 幂等 | `…::test_idempotent_client_msg_id_returns_cached_turn` |
| 工具（提醒/表情） | `…::test_tool_turn_creates_reminder` / `test_meme_turn_uses_meme_mcp` |
| 隔离 | `tests/test_isolation.py` |
| 重试/单飞/校验 | `tests/test_reliability.py` |
| 注册中心 | `tests/test_skill_registry.py` |
| 护栏/裁剪 | `tests/test_context_and_guard.py` |
| HTTP 契约 | `tests/test_api.py` |
| 归属隔离 | `tests/test_auth.py` |
