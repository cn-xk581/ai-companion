# 架构说明 · AI 伴侣 Agent v2.1

> 目标：让一个「记得你」的陪伴 Agent 能在生产跑住 ——
> 数字与规则可解释、崩溃能续跑、越权进不来、每一步能查、卸载能删干净。

---

## 1. 设计不变量

| 不变量 | 含义 | 违反后果 |
|---|---|---|
| **I1 状态入图，不进提示词** | 人设/阶段/分数/情绪是持久状态，由节点读取与写回 | 人设漂移、关系忽冷忽热且无法解释 |
| **I2 业务规则只在 Skill** | 风控分级、阶段尺度、关系推进、记忆筛选都在 Skill | 规则散落、不可测、改一处漏三处 |
| **I3 MCP 不含业务** | 适配器只做连接/协议/类型转换/错误归一化 | 换数据源要改业务 |
| **I4 状态可序列化** | state 只放 dict/list/str/number，领域模型在节点边界转换 | 检查点读不出来、断点续跑失效 |
| **I5 外部调用可重试、可合并、可审计** | 统一走 MCPClient / SkillExecutor | 重试散落、成本失控、事故不可复盘 |

---

## 2. 分层

```
接入层  FastAPI（鉴权 / 限流 / 契约 / 错误码）
   ↓
编排层  ChatService + LangGraph（7 节点；只做状态转换与路由）
   ↓
业务层  Skill（人设校验、风控分级、关系/情绪推进、回复润色、提醒/画图/表情的业务包装）
   ↓
适配层  MCP（calendar / image_gen / meme_library，只对接外部资源）
   ↓
PostgreSQL · Redis · 本地文件 · 第三方 HTTP
```

边界口诀：

- 「产品/运营会改的规则」→ Skill；
- 「这是外部系统的一个接口」→ MCP；
- 「这决定下一步走哪条边」→ 节点；
- 「谁能调用什么」→ 执行器 + Agent 白名单。

反例（评审会打回）：节点里判断越界尺度、适配器里判断关系阶段、Skill 里直连数据库。

---

## 3. 一轮对话的时序

```
client            api           ChatService        graph                    skill/mcp
  │ POST /chat/stream │               │               │                        │
  ├──────────────────►│ 限流+鉴权      │               │                        │
  │                   ├──────────────►│ 幂等键/会话锁   │                        │
  │                   │               ├─ 组装 state（人设/阶段/分数/情绪/历史）  │
  │                   │               ├─ ainvoke(thread=user:session:turn)      │
  │                   │               │               ├─ intake ──────────────► persona.normalize
  │                   │               │               │                         safety.guard(input)
  │                   │               │               │                         memory.recall
  │                   │               │               ├─ plan（LLM：要不要用工具）
  │                   │               │               ├─ execute ─────────────► reminder/image/meme ─► MCP
  │                   │               │               ├─ respond（LLM 流式 + reply.polish）
  │                   │               │               ├─ safety_check（输出风控）
  │                   │               │               │    └ review ─► human_gate（interrupt）
  │◄──SSE token*──────┤               │               └─ persist（情绪/关系/记忆/落库）
  │◄──done────────────┤               │
```

风控短路：输入命中注入/违法/越界 → 不进 LLM；自伤倾向 → 关怀话术 + 人工复核；
输出越界 → 人工复核（approve 发、reject 换安全话术）。

---

## 4. 长任务可靠性

### 4.1 检查点与恢复

- thread_id = `user_id:session_id:turn_id`：一轮一个线程，既是恢复主键也是租户隔离边界；
- 人工复核：`interrupt(payload)` 冻结草稿 → `POST /turns/{id}/resume` 用 `Command(resume=…)` 继续，
  **intake/respond 不会重跑**（有测试断言 span 数不变）；
- 三档后端：`AsyncPostgresSaver`（生产）→ `AsyncSqliteSaver`（单机）→ `InMemorySaver`（测试），
  启动探针会立刻读一次空 thread，避免「启动正常、首个任务才炸」。

### 4.2 SingleFlight

- 进程内 Future + Redis `SET NX` 两段式；等待超时 fail-open；
- key = `namespace + sha1({version, args})`；
- **`cache_ttl=0` 表示只合并并发、不缓存结果**（写后立刻读必须看到新数据；这里踩过坑，见 §8）。

### 4.3 错误分类与重试

| 类型 | 判定 | 重试 | 例子 |
|---|---|---|---|
| `TRANSIENT` | 超时/连接/5xx | ✅ 指数退避+抖动 | LLM 502、MCP 超时 |
| `RATE_LIMIT` | 429 | ✅ 尊重 Retry-After | 上游限流 |
| `VALIDATION` | Schema/4xx | ❌ | 工具参数不合法 |
| `PERMISSION` | 白名单 | ❌ | replier 调提醒 |
| `BUSINESS` | 业务拒绝 | ❌ | 提醒时间已过 |
| `INTERNAL` | 未知 | ❌ | 代码缺陷（告警） |
| `HUMAN` | 需人工 | ❌ | 自伤/越界草稿 |

### 4.4 防死循环

轮次（`GRAPH_MAX_ROUNDS`）、预算（skill/llm/墙钟）、重复调用签名（`GRAPH_REPEAT_LIMIT`）、
无进展（`GRAPH_NO_PROGRESS_LIMIT`）；护栏状态随检查点持久化，进程重启不丢计数。

---

## 5. 可观测与审计

`turn_events` 只追加，`GET /turns/{id}/trace` 读出：

```json
{"type":"span","kind":"llm","name":"llm.reply","duration_ms":812.5,"status":"ok",
 "attrs":{"model":"deepseek-chat","temperature":0.85,"prompt_digest":"a1b2c3",
          "prompt_preview":"…","response_preview":"…","tokens_in":812,"tokens_out":96}}
{"type":"span","kind":"skill","name":"skill.safety.guard","duration_ms":1.1,
 "attrs":{"args":"{…}","output":"{…}","attempts":1}}
{"type":"decision","action":"human.requested","payload":{"categories":["self_harm"]}}
```

- LLM：入参/出参摘要、token、模型、温度、重试次数；
- Skill：白名单结果、入参、出参、是否合并、尝试次数、失败分类；
- MCP：适配器、工具、耗时、缓存/合并命中；
- 失败可定位：span 带 `error {kind,type,message}`；`trace_summary` 汇总 token/次数/重试。

---

## 6. 上下文与幻觉治理

- 提示词只放：人设、阶段策略、情绪、长期记忆、工具结果、最近 `HISTORY_WINDOW` 条历史；
  硬约束与最近输入永不裁，超预算才折摘要；
- LLM 输出只允许两类 JSON：工具计划（`TurnPlan`）与记忆抽取（`MemoryExtract`），
  结构不对 → 修复一次 → 仍不对就按纯聊天/不记忆处理；
- 工具参数过 Skill 的 Input 模型，不合法当场拦截，绝不带脏参数调外部系统；
- 回复过 `reply.polish`：去出戏句、按阶段收亲昵称呼、按句截断、空回复兜底；
- 输出过 `safety.guard`：越界 → 人工复核。

---

## 7. 隔离矩阵

| Agent | Skill | MCP |
|---|---|---|
| companion_intake | persona.normalize / safety.guard / memory.recall | — |
| companion_replier | reply.polish / memory.recall | — |
| companion_tools | reminder.create / image.reply / meme.reply | calendar.* / image.* / meme.* |
| companion_state | mood.update / relationship.update / memory.remember | — |

- 共享：`memory.recall`（intake + replier）；
- 隔离：replier 调 `reminder.create` → Skill 白名单拒绝；即使 Skill 在白名单，
  其依赖的 `calendar.*` 不在该 Agent 的 MCP 白名单也会被拒（测试覆盖）。

---

## 8. 改动清单（v2.0 配餐版 → v2.1 伴侣版）

| 改动 | 目的 | 解决的问题 | 风险 → 缓解 |
|---|---|---|---|
| 领域换回伴侣（domain/skills/mcp/agents/图/API/前端） | 回到项目本意：AI 伴侣 | 配餐域不是目标形态 | 工程层大改 → 复用 v2 骨架，只换领域；55 用例回归 |
| 新增 `persona/safety/relationship/mood/reply` 五个状态类 Skill | 规则从提示词搬进代码 | 人设漂移、尺度失控 | 规则过硬 → 参数化 + 单测 |
| 新增 `reminder.create / image.reply / meme.reply` | 让她能干活 | 陪伴只有嘴没有手 | 外部依赖失败 → 能力未启用话术、不阻断对话 |
| MCP 三个适配器（calendar/image/meme） | 外部资源标准化 | 直连第三方难替换 | 本地 JSON 存储 → 生产替换飞书/对象存储 |
| `turn_events` 按轮次落库 | 单轮可审计 | 会话级日志粒度太粗 | 事件量 → 预览截断 + 上限 |
| 输出侧人工复核（interrupt） | 高风险内容不直接发 | 消费者产品的内容责任 | 审核延迟 → 仅 borderline 触发，其余自动通过 |
| 修复 SingleFlight `ttl=0` 被当成默认缓存 | 写后读一致性 | 建提醒后列表读到旧值（真实踩到） | 显式 0 = 不缓存；`calendar.list` 关闭缓存 |
| 分类重试/单飞/检查点/护栏沿用 | 可靠性不随域退化 | 每次改域重造轮子 | 与领域解耦，回归用例保护 |

---

## 9. 生产配置要点

```ini
ENVIRONMENT=prod
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
IMAGE_MODEL=            # 留空 = 不启用画图
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/companion
REDIS_URL=redis://127.0.0.1:6379/0
VECTOR_STORE_BACKEND=pgvector
EMBEDDING_BACKEND=sentence-transformers
CHECKPOINT_BACKEND=auto
ADMIN_TOKEN=<openssl rand -hex 24>
JWT_SECRET=<openssl rand -hex 32>
TRUST_PROXY_HEADERS=true
```

生产启动校验会拒绝：缺 LLM key / 缺 PG / 缺 Redis / hash 嵌入 / 内存向量库 / 内存检查点 /
默认 JWT / 缺 ADMIN_TOKEN。

---

## 10. 已知限制

1. **情绪/关系是确定性规则**：可解释、可测试，但不如模型细腻，后续可加轻量分类器；
2. **记忆召回质量取决于嵌入模型**：默认 hash 仅演示；
3. **画图依赖外部模型服务**，未配置时能力关闭（不伪造成功）；
4. **审计事件无 TTL**：需按保留策略归档；
5. **前端支持多会话，但无分页/虚拟滚动**：同一时刻只渲染一个会话的聊天区；历史消息上限 300 条、
   会话列表全量拉取；多设备同步/消息已读等产品功能未做。
