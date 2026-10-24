from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 服务根目录下的 .env，便于本地覆盖连接串
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """法律平台演示业务服务配置。

    该服务只负责业务事实与受控写入，不参与会话、AI 推理与实时推送。
    """

    database_url: str = (
        "postgresql+psycopg://legal:legal_dev_password@127.0.0.1:55432/legal_business"
    )
    api_host: str = "0.0.0.0"
    api_port: int = 8001
    jwt_secret: str = "legal-demo-secret"
    jwt_algorithm: str = "HS256"
    # 演示账号令牌有效期；生产环境由平台身份体系签发
    demo_token_expire_minutes: int = 720
    # 演示数据开关：测试环境可关闭种子导入后自行构造夹具
    seed_demo_data: bool = True
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        # vite preview 构建产物预览端口
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:4174",
        "http://127.0.0.1:4174",
    ]

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
