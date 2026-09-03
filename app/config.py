"""全局配置：pydantic-settings 读取 .env，全部有默认值，无外部依赖也能跑。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "AI Girlfriend Agent"
    environment: str = "dev"
    log_level: str = "INFO"

    # ---- LLM / 多模态 ----
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.9
    llm_max_tokens: int = 1024
    embedding_model: str = "BAAI/bge-small-zh-v1.5"  # sentence-transformers 模型名 / openai 模型 id
    embedding_backend: str = "hash"  # hash | sentence-transformers | openai
    embedding_device: str = ""       # cpu | cuda | 留空自动
    hf_endpoint: str = ""            # 留空用 huggingface.co；国内可设 https://hf-mirror.com
    image_model: str = ""      # 留空 -> 画图工具返回"未配置"

    # ---- 存储 ----
    database_url: str = ""     # 留空 -> 内存仓库降级
    redis_url: str = ""        # 留空 -> 内存缓存降级
    vector_store_backend: str = "memory"  # memory | pgvector | milvus

    # ---- 图参数 ----
    memory_retrieve_top_k: int = 5
    memory_importance_threshold: float = 0.1  # hash 降级 embedding 相似度偏低；用真实模型可调高到 0.3~0.5
    history_window: int = 12
    max_reply_length: int = 400

    # ---- 主动推送（独立后台任务） ----
    active_push_enabled: bool = False
    active_push_interval_sec: int = 600
    active_push_max_per_session_per_day: int = 3
    active_push_chance: float = 0.3

    # ---- 风控 / 限流 ----
    chat_rate_limit_per_min: int = 30
    session_ttl_sec: int = 7 * 24 * 3600

    # ---- 会话锁 / 幂等 ----
    lock_acquire_timeout_sec: float = 10.0  # 等待获取会话锁的最长时间
    lock_ttl_sec: int = 120                 # 锁租约：须 > 等待超时，且 > 单轮最长处理耗时
    idempotency_ttl_sec: int = 3600         # 幂等结果缓存时长

    # ---- 认证限流 / 防爆破 ----
    auth_rate_limit_per_min: int = 10       # 每 IP 每分钟认证请求上限（注册+登录共用）
    login_max_failures: int = 5             # 连续失败次数阈值，达到则锁定账号
    login_lockout_sec: int = 900            # 账号锁定时长（秒）
    trust_proxy_headers: bool = False       # 仅部署在可信反代后置 True，才信任 X-Forwarded-For

    # ---- JWT 鉴权 ----
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 7 * 24 * 60  # 7 天


@lru_cache
def get_settings() -> Settings:
    return Settings()
