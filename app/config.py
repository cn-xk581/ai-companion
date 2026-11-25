"""全局配置：pydantic-settings 读取 .env。所有项都有默认值，无外部依赖也能起服务。

配置分区与「改它会怎样」的说明
------------------------------
- LLM / 存储：沿用原工程，不赘述；
- graph_*：护栏参数，**改小**等于更保守（更早转人工），改大要评估成本；
- retry_* / singleflight_*：可靠性参数，生产建议保持默认或更保守；
- checkpoint_*：断点续跑后端，auto 会按可用性依次尝试 postgres → sqlite → memory；
- skill_*：注册中心刷新与插件包；
- mcp_*：外部资源适配器。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "AI 伴侣 Agent"
    domain: str = "companion"
    environment: str = "dev"
    log_level: str = "INFO"

    # ---- LLM ----
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.85
    llm_max_tokens: int = 600
    image_model: str = ""  # 留空 => 画图能力不启用

    # ---- 伴侣域参数 ----
    history_window: int = 12          # 注入提示词的最近消息条数
    reply_max_chars: int = 220        # 单条回复软上限（超出按句截断）
    memory_top_k: int = 5             # 每轮召回几条长期记忆
    memory_min_score: float = 0.1     # hash 嵌入下用 0.1；真实 bge 可调 0.35~0.5
    memory_extract_enabled: bool = True
    memory_extract_max: int = 3       # 每轮最多沉淀几条
    session_ttl_sec: int = 7 * 24 * 3600

    # ---- Embedding（记忆召回）----
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_backend: str = "hash"  # hash | sentence-transformers | openai
    embedding_device: str = ""
    hf_endpoint: str = ""

    # ---- 存储 ----
    database_url: str = ""       # 空 => 内存仓库
    redis_url: str = ""          # 空 => 内存缓存 / 内存 Skill 注册中心
    vector_store_backend: str = "memory"  # memory | pgvector | milvus

    # ---- 图护栏（防死循环 / 防跑飞）----
    graph_max_rounds: int = 3
    graph_max_skill_calls: int = 12
    graph_max_llm_calls: int = 8
    graph_max_wall_seconds: float = 120.0
    graph_repeat_limit: int = 2
    graph_no_progress_limit: int = 2
    max_skill_calls_per_round: int = 4

    # ---- 重试 ----
    retry_max_attempts: int = 3
    retry_base_delay: float = 0.4
    retry_max_delay: float = 8.0
    retry_jitter: float = 0.25

    # ---- SingleFlight ----
    singleflight_enabled: bool = True
    singleflight_ttl: int = 120
    singleflight_wait_sec: float = 8.0

    # ---- Checkpoint（断点续跑）----
    checkpoint_backend: str = "auto"          # auto | postgres | sqlite | memory
    checkpoint_sqlite_path: str = "data/checkpoints.sqlite3"

    # ---- Skill 注册中心 ----
    skill_refresh_sec: float = 5.0
    skill_plugin_packages: str = ""           # 逗号分隔的包名，包内暴露 SKILLS = [...]

    # ---- MCP ----
    mcp_data_dir: str = ""                    # 空 => app/mcp/data（表情包等静态目录）
    mcp_state_dir: str = "data/mcp"           # 运行期存储（提醒等），随安装目录一起清理
    mcp_http_timeout_sec: float = 3.0

    # ---- Trace / 审计 ----
    trace_enabled: bool = True
    trace_preview_chars: int = 400
    trace_max_spans: int = 500

    # ---- 上下文治理 ----
    context_max_tokens: int = 3000
    context_keep_recent: int = 6
    context_summary_max_chars: int = 400
    context_summary_llm_enabled: bool = False

    # ---- 接入层限流 / 幂等 ----
    chat_rate_limit_per_min: int = 30
    lock_acquire_timeout_sec: float = 10.0
    lock_ttl_sec: int = 180
    idempotency_ttl_sec: int = 3600

    # ---- 认证 ----
    auth_rate_limit_per_min: int = 10
    login_max_failures: int = 5
    login_lockout_sec: int = 900
    trust_proxy_headers: bool = False

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 7 * 24 * 60

    # ---- 运维接口 ----
    admin_token: str = ""  # 空 => 运维接口关闭（403），生产必须显式配置

    @property
    def plugin_packages(self) -> list[str]:
        return [p.strip() for p in self.skill_plugin_packages.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
