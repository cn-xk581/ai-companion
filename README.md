# AI 女友 Agent · LangGraph 整体框架

> 把 **关系阶段、情绪、记忆、对话上下文全部放进 Graph State**，不再靠单纯 Prompt 硬扛全部状态，从根源上解决人设漂移。

## 架构分层

```
接入层 (FastAPI /chat) → 会话调度层 (SessionManager)
        ↓
LangGraph Agent 核心图（8 节点 + 条件边）
        ↓
记忆子系统 → 工具子系统 → 安全层 → LLM / 多模态模型层 → 存储层
```

## 整体数据流

```
用户输入 → FastAPI 接口 → 会话管理器（限流 + 会话锁 + 快照加载）
    ↓
LangGraph State（携带全部运行时状态）
    ↓
├─ memory_retrieve_node  记忆检索：向量库取相关长期记忆，importance 过滤 top-N
├─ state_guard_node      状态守卫：关系阶段亲密度校验 + 提示注入检测 + 情绪微更新
│                          （触发风控 → 直接跳输出兜底，不再走 LLM）
├─ agent_planner_node    规划器(LLM)：判断 直接回复 / 调用工具（条件边）
├─ tool_exec_node        工具执行：画图 / 真心话大冒险 / 纪念日提醒 / 表情包
├─ generate_reply_node   生成回复：人设 + 情绪 + 关系阶段 + 记忆 + 滑动窗口历史 + 输入
├─ memory_extract_node   记忆抽取(异步)：LLM 提取值得记住的事实，打 importance
├─ output_safety_node    输出校验兜底：越界/崩坏 → fallback，限长
└─ persist_node          持久化(后台异步)：历史→PG，记忆→向量库+PG，关系/情绪→PG+Redis
    ↓
返回给用户；埋点日志；异步持久化不阻塞主链路
```

## 目录结构

```
app/
├── main.py                    # FastAPI 入口（lifespan 装配 + 托管前端产物 + 主动推送任务）
├── config.py                  # pydantic-settings 配置（全默认值，无外部依赖可跑）
├── api/                       # 接入层：/chat /session/* /memory/* /persona/update
├── services/session_manager.py  # 会话调度：组装 State、Redis 缓存快照、调 graph.ainvoke
├── graph/
│   ├── state.py               # Pydantic AgentState（关系/情绪/记忆/上下文全在 State）
│   ├── builder.py             # 八节点 + 条件边装配
│   ├── context.py             # GraphContext：LLM/存储/向量库依赖注入
│   └── nodes/                 # 八个节点（每个都是 state -> 部分更新 的函数）
├── persona/                   # 人设、关系阶段(含亲密度级别)、情绪模型、分数→阶段跃迁
├── memory/                    # 向量库抽象(memory/pgvector/milvus) + 记忆抽取 + 工厂
├── tools/                     # 工具子系统：registry + 4 个内置工具
├── safety/                    # 输入风控(注入/越级) + 输出校验兜底 + 情绪更新
├── llm/                       # 模型层：对话 LLM + Embedder（开源本地/OpenAI 兼容/hash 降级）
├── storage/                   # PostgreSQL 仓库 / Redis 缓存 / 对象存储（均带内存降级）
└── scheduler/active_push.py   # 主动消息推送（独立后台任务，不跑在对话链路内）

frontend/                      # 微信风格 Web 前端（Vue 3 + Vite，构建产物由 FastAPI 托管）
├── vite.config.js             # dev 代理 /api → http://127.0.0.1:8000
└── src/
    ├── App.vue                # 主布局：登录 / 会话列表 + 聊天窗口
    ├── api.js                 # REST + SSE 流式客户端
    ├── store/auth.js          # JWT 登录态（localStorage）
    ├── constants.js           # 关系阶段/情绪标签、表情
    └── components/            # LoginView / ChatList / ChatWindow / MessageBubble / SettingsDrawer

deploy/                        # 云服务器一键部署（见「云服务器部署」章节）
├── start.sh                   # 一键启动完整栈：infra + 依赖 + 前端构建 + systemd 服务
├── stop.sh                    # 停止后端（--all 连基础设施一起停）
├── redeploy.sh                # 重部署：git pull → 重装依赖 → 重建前端 → 重启
├── common.sh                  # 共享工具：.env 校验 / systemd / nohup 降级
└── aigf.service               # systemd 单元模板（start.sh 替换占位符后安装）

tests/                         # 离线测试：StubLLM 全链路（直答/工具/风控/记忆检索/鉴权/流式）
examples/chat_demo.py          # 本地演示（需真实 LLM_API_KEY）
```

## 快速开始

```bash
# 1. 基础设施（可选：不启动也能本地跑，自动降级为内存实现）
docker compose up -d

# 2. 安装依赖
python -m venv .venv
.\.venv\Scripts\activate        # Windows；Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

# 3. 配置（必须：已移除 FakeLLM，需真实模型）
copy .env.example .env          # 填入 LLM_API_KEY、JWT_SECRET 等

# 4. 跑测试
pytest -v

# 5. 启动服务
aigf-serve                      # 或 uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000/docs
```

## 云服务器部署（完整栈）

> 一键脚本：`deploy/start.sh` / `deploy/stop.sh` / `deploy/redeploy.sh`，配合 systemd 常驻。
> **应用本体不打包镜像**：Docker 只负责基础设施（Postgres+pgvector、Redis），应用跑在宿主机。

### 前置要求（服务器）

- Docker + Docker Compose v2、Python 3.10+
- Node（仅前端构建需要；也可本地 `npm run build` 后把 `frontend/dist` 传上去）
- 对外放行 80/443（走 nginx 反代）；调试期可临时放行 8000

### 1. 拉代码 + 写 `.env`

```bash
git clone <仓库地址> aigf && cd aigf
cp .env.example .env
openssl rand -hex 32        # 生成 JWT_SECRET
```

`.env` 生产要点（`ENVIRONMENT=prod` 会强制校验，缺任一关键项即拒绝启动）：

```ini
ENVIRONMENT=prod
LLM_API_KEY=sk-...                      # 真实 key
EMBEDDING_BACKEND=sentence-transformers # 生产禁止 hash
HF_ENDPOINT=https://hf-mirror.com       # 国内下载 bge 模型走镜像
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/aigf
REDIS_URL=redis://127.0.0.1:6379/0
VECTOR_STORE_BACKEND=pgvector           # 生产禁止 memory
JWT_SECRET=<openssl rand -hex 32 的结果>
# 部署在反代后置 true，否则限流会把所有请求识别成 nginx 内网 IP
TRUST_PROXY_HEADERS=true
```

⚠️ 上云前改掉 `docker-compose.yml` 里 Postgres 的默认密码（`postgres/postgres`），并同步修改 `DATABASE_URL`。

### 2. 一键启动

```bash
bash deploy/start.sh
```

脚本自动完成：`docker compose up -d` 起基础设施 → 等 Postgres 就绪 → 创建 `.venv` 并安装依赖（首次含 torch，较慢）→ `npm run build` 构建前端 → 生成并启用 systemd 服务 `aigf`。

启动后访问：

- 聊天界面：`http://服务器IP:8000/`（有 nginx 反代则走 80/443）
- API 文档：`http://服务器IP:8000/docs`
- 健康检查：`curl http://127.0.0.1:8000/api/v1/health`

> 默认后端只监听 `127.0.0.1`，对外必须配 nginx 反代（见下）。想临时直接对外暴露可 `BIND_HOST=0.0.0.0 bash deploy/start.sh`（不推荐，无 TLS）。

### 3. 停止 / 重部署

```bash
bash deploy/stop.sh          # 只停后端，保留 Postgres/Redis（数据不丢）
bash deploy/stop.sh --all    # 后端 + 基础设施一起停（数据卷保留）

bash deploy/redeploy.sh      # git pull → 重装依赖 → 重建前端 → 重启服务
```

### 4. nginx 反代 + HTTPS（推荐）

systemd 单元把后端绑到 `127.0.0.1:8000`，生产用 nginx 终结 TLS（**SSE 必须关缓冲**）：

```nginx
server {
    listen 80;
    server_name aigf.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;          # SSE 流式必须关闭，否则回复一次性吐出
        proxy_read_timeout 300s;
    }
}
```

HTTPS：`certbot --nginx -d aigf.example.com`。记得 `.env` 里 `TRUST_PROXY_HEADERS=true`。

### 手动跑（不用脚本）

```bash
docker compose up -d
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[embed]"                  # embedding 走 OpenAI 兼容服务时可只装基础依赖
python -m examples.verify_pgvector         # 可选：一键验证 pgvector 建表/写入/检索
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 会话锁/幂等/认证限流等参数（`LOCK_*`、`IDEMPOTENCY_TTL_SEC`、`AUTH_*`）都有默认值，按需在 `.env` 覆盖即可。

## 微信风格 Web 前端（frontend/）

Vue 3 + Vite 单页应用，仿微信桌面端布局：左侧会话列表 + 右侧聊天窗口（流式打字回复、关系阶段/情绪状态栏、表情面板、人设/记忆设置抽屉）。

```bash
# 方式一（推荐，生产/联调）：构建后由 FastAPI 直接托管，打开 http://127.0.0.1:8000 即进入界面
cd frontend
npm install
npm run build
# 之后正常启动后端：aigf-serve（或 uvicorn app.main:app --reload）

# 方式二（开发，热更新）：Vite dev server 自带 /api 代理
cd frontend
npm run dev                    # http://127.0.0.1:5173
# 后端需同时运行在 8000 端口
```

界面功能：注册/登录 → 会话列表（头像/昵称/最后消息/时间）→ 点击进入聊天（SSE 流式）→ 右上角「⋯」打开设置抽屉（编辑人设、查看/删除记忆、重置关系）。页面顶部状态栏实时显示**关系阶段、亲密度、情绪**。

**先注册/登录拿 token，再调其它接口**：

```bash
# 注册（返回 access_token）
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret123"}'

# 聊天（带上 token）
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","user_input":"在吗？今天有点想你"}'
```

## API 一览（除 /auth/* 与 /health 外均需 `Authorization: Bearer <token>`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register` | 注册：`{username, password}` → JWT |
| POST | `/api/v1/auth/login` | 登录：`{username, password}` → JWT |
| GET | `/api/v1/auth/me` | 当前用户信息 |
| GET | `/api/v1/session/list` | 我的会话列表（含昵称/最后消息/关系/情绪，供前端会话列表） |
| POST | `/api/v1/session/create` | 显式创建会话（幂等；新会话「打开设置」前先调用，避免详情/记忆 404） |
| GET | `/api/v1/session/detail?session_id=` | 会话详情（完整人设 + 关系 + 情绪） |
| GET | `/api/v1/session/history?session_id=&limit=` | 会话聊天历史（前端加载过往消息） |
| POST | `/api/v1/chat` | 聊天（一次性返回）：`{session_id?, user_input}` |
| POST | `/api/v1/chat/stream` | 聊天（SSE 流式，`token` 事件 + 末尾 `done` 事件） |
| POST | `/api/v1/session/reset` | 一键重置记忆、关系状态（仅本人） |
| GET | `/api/v1/memory/list?session_id=` | 查看长期记忆（仅本人） |
| POST | `/api/v1/memory/delete` | 删除单条记忆（仅本人） |
| PUT | `/api/v1/persona/update` | 动态修改人设参数（仅本人） |
| GET | `/api/v1/health` | 健康检查（公开） |

安全：所有受保护端点做 JWT 鉴权 + **session 归属校验**（session 强绑定 user_id，越权返回 403）；`/chat` 内置 Redis 滑动窗口限流 + 会话锁。

## 存储层分工

| 组件 | 职责 | 降级方案 |
|---|---|---|
| PostgreSQL | session / 用户 / 结构化记忆 / 关系阶段 / 情绪持久化 | 未配置 → 进程内内存仓库 |
| 向量库 | 记忆片段 embedding 相关性检索（`memory` / `pgvector` / `milvus` 可插拔） | 默认内置余弦相似度实现 |
| Redis | 短期会话缓存、限流、会话锁、主动推送频率控制 | 未配置 → 进程内内存缓存 |
| 对象存储 | 生成图片、表情包资源（S3/OSS 可替换 `ObjectStore.upload_bytes`） | 本地目录落盘 |

### 上 pgvector（推荐的生产向量库）

```bash
# 1. 启动 pgvector + Redis（镜像内置 pgvector 扩展）
docker compose up -d

# 2. .env 配置（已内置，确认即可）
#    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aigf
#    REDIS_URL=redis://localhost:6379/0
#    VECTOR_STORE_BACKEND=pgvector

# 3. 一键验证（建表 + 建扩展 + 写入向量 + 余弦检索排序）
python -m examples.verify_pgvector

# 4. 正常启动（启动时自动 CREATE EXTENSION IF NOT EXISTS vector + 建表）
python -m uvicorn app.main:app --reload
```

- 用**原始 SQL** 直接调 pgvector 的 `<=>` 余弦距离算子，**无需额外 Python 包**（`asyncpg` 已含在依赖里）。
- 记忆检索 = `1 - (embedding <=> query)`，等价余弦相似度；`memory_vectors` 表独立于结构化 `memories` 表，专供检索。
- ⚠️ **换 embedding 模型会改变向量维度**，需重建向量列：
  ```sql
  -- 例：从 bge-small(512) 换到 bge-base(768)
  ALTER TABLE memory_vectors ALTER COLUMN embedding TYPE vector(768);
  ```

> 内存降级仅用于演示/测试/单机起步；商用请配置 `DATABASE_URL`、`REDIS_URL`、`VECTOR_STORE_BACKEND`。**`ENVIRONMENT=prod` 时会显式拒绝内存降级 / 缺失 Key / 缺失 JWT_SECRET，杜绝误上线。**

## 嵌入模型（记忆检索的核心）

embedding 已与对话 LLM 解耦，`EMBEDDING_BACKEND` 三选一：

| 后端 | 说明 | 适用 |
|---|---|---|
| `sentence-transformers` | 本地开源模型，默认 `BAAI/bge-small-zh-v1.5`（中文好、体积小、CPU 可跑） | **推荐**，无需外部 API |
| `openai` | OpenAI 兼容 embedding API（可对接自托管 TEI/vLLM/Ollama 等开源模型服务） | 已有向量服务时 |
| `hash` | 内置哈希降级，无依赖 | 仅演示/离线 |

```ini
EMBEDDING_BACKEND=sentence-transformers
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5   # 或 bge-base-zh-v1.5 / bge-m3
HF_ENDPOINT=https://hf-mirror.com        # 国内下载模型镜像
```

- 首次启动会自动下载模型（bge-small-zh 约 95MB）；需 `pip install ".[embed]"`（含 sentence-transformers + CPU torch）。
- 换更大模型直接改 `EMBEDDING_MODEL`，向量维度自动跟随；**注意：换模型会改变向量维度，pgvector/milvus 需重建向量列/集合**。
- bge 检索时自动加查询前缀（`为这个句子生成表示以用于检索相关文章：`），文档侧不加。
- 用真实嵌入时把 `MEMORY_IMPORTANCE_THRESHOLD` 调到 0.35~0.5；hash 降级用 0.1。

## 关键设计

1. **状态全入 State**：`AgentState` 携带 `persona / relationship_stage / relationship_score / mood / chat_history / retrieved_memories / new_memories / tool_calls / agent_reply / reject_reason`，节点是 `state -> 部分更新` 的纯函数，人设不再靠 Prompt 硬扛。
2. **关系阶段跃迁**：`persist` 按情绪/风控结果微调 `relationship_score`，跨过阈值自动升级阶段（stranger → acquaintance → crush → dating → couple），亲密度尺度随阶段变化，并把新分数回写响应。
3. **记忆不膨胀**：`memory_extract` 由 LLM 抽取 + importance 加权，闲聊直接丢弃；检索只取 top-N 注入 Prompt，避免上下文爆炸。
4. **风控短路**：提示注入/越级内容在 `state_guard` 直接拦截，不经 LLM 生成，直接输出 fallback。
5. **JWT 用户体系 + 会话归属**：PBKDF2 密码哈希 + JWT 签发/校验；session 强绑定 user_id，所有数据操作校验归属。
6. **可靠持久化**：`persist` 内联等待（先落库、再回包），去掉 fire-and-forget 的丢数据竞态。
7. **流式回复**：`generate_reply` 通过 `LLMClient.stream` + LangGraph `get_stream_writer` 边生成边推 SSE。
8. **主动推送独立**：`scheduler/active_push.py` 是单独后台定时任务，带每日次数上限 + 概率 + 间隔控制，通过 `ctx.notifier` 回调接入层下发。

## 扩展指南

- **换模型**：实现 `app/llm/base.py` 的 `LLMClient` 接口（或直接用 `OpenAILLMClient` 指向任意 OpenAI 兼容网关）。
- **加工具**：在 `app/tools/` 新增 `SCHEMA + handler(args, ctx, session_id)`，注册进 `registry.py` 即可自动进入规划器的 function calling。
- **加向量库**：实现 `app/memory/vector_store.py` 的 `VectorStore` 接口，在 `factory.py` 注册后端。
- **推送渠道**：给 `GraphContext.notifier` 注入 WebSocket 管理器 / Webhook / 推送 SDK。
- **生产风控**：内置规则是启发式兜底，生产建议叠加内容安全模型（审核服务）在 `state_guard` / `output_safety` 上。
