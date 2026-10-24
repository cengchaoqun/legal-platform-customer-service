from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://legal:legal_dev_password@127.0.0.1:55432/customer_service"
    )
    redis_url: str = "redis://127.0.0.1:56379/0"
    ai_service_url: str = "http://127.0.0.1:8002"
    # 整轮 AI 预算：必须小于 Worker 租约，并给网络重试留出余量
    ai_timeout_seconds: int = 60
    # 与 AI Service 必须完全一致，部署时通过环境变量统一注入
    internal_service_token: str = "legal-support-internal-token"
    internal_service_jwt_secret: str = "legal-support-internal-jwt"
    internal_service_jwt_algorithm: str = "HS256"
    # 与演示业务服务、AI Service 使用同一个开发密钥
    jwt_secret: str = "legal-support-local-secret"
    jwt_algorithm: str = "HS256"
    api_host: str = "0.0.0.0"
    api_port: int = 8010
    conversation_idle_timeout_minutes: int = 30
    message_merge_delay_ms: int = 800
    message_merge_max_wait_ms: int = 2000
    ai_worker_poll_interval_ms: int = 100
    # Worker 租约需要覆盖“决策 + 工具 + 纠错”的整轮预算；Worker 在运行期间会续租
    ai_worker_lease_seconds: int = 180
    ai_worker_max_attempts: int = 3
    ai_worker_retry_delay_seconds: int = 2
    # 模型/工具失败达到上限时，除固定提示外是否自动创建人工工单
    ai_fallback_handoff: bool = True
    outbox_worker_poll_interval_seconds: float = 1
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        # vite preview 构建产物预览端口
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:4174",
        "http://127.0.0.1:4174"
    ]

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
