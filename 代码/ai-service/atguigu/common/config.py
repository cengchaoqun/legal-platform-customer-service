from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """AI Service 运行配置。

    关键约束：
    1. 在线客服通过 DeepSeek API 推理，密钥由环境变量注入；
    2. 只有显式开启且运行在 dev/test 环境时才允许 fixture 决策器；
    3. 整轮预算与 Customer 的 Worker 租约、HTTP 超时保持同一量级。
    """

    app_env: Literal["dev", "test", "prod"] = "dev"
    ai_database_url: str = (
        "postgresql+psycopg://legal:legal_dev_password@127.0.0.1:55432/ai_customer_service"
    )
    api_host: str = "0.0.0.0"
    api_port: int = 8002
    internal_service_token: str = "legal-support-internal-token"
    jwt_secret: str = "legal-support-local-secret"
    jwt_algorithm: str = "HS256"

    # 法律平台（没有真实接口时指向本地演示业务服务）
    legal_platform_base_url: str = "http://127.0.0.1:8001/api/v1"
    legal_platform_timeout_seconds: float = 8.0

    # 模型与推理部署
    llm_provider: Literal["deepseek"] = "deepseek"
    llm_model: str = "deepseek-flash"
    llm_api_key: str = Field(default="", validation_alias=AliasChoices("DEEPSEEK_API_KEY", "LLM_API_KEY"))
    llm_base_url: str = "https://api.deepseek.com"
    llm_thinking_mode: Literal["disabled", "enabled"] = "disabled"
    llm_timeout_seconds: float = Field(default=30, gt=0)
    llm_healthcheck_enabled: bool = True
    llm_healthcheck_timeout_seconds: float = 15.0
    # 保留既有审计列，记录 API 版本而不再表示训练产物。
    model_artifact_version: str = "deepseek-api-v1"
    model_deployment_id: str = "deepseek-cloud"
    adapter_version: str = "not-applicable"

    # deployed 使用真实 DeepSeek；fixture 仅用于离线回归测试
    policy_runtime_mode: Literal["deployed", "fixture"] = "deployed"
    allow_fixture_model: bool = False
    # 是否已经配置真实专业法律咨询承接队列；未配置时只转平台售后队列
    professional_review_available: bool = False

    # 契约与版本
    contract_version: str = "legal-support-v2"
    flow_definition_version: str = "legal-support-flow-v1"
    policy_prompt_version: str = "legal-policy-v1"
    execution_prompt_version: str = "legal-execution-v1"
    history_message_limit: int = Field(default=30, ge=1, le=60)
    history_character_budget: int = Field(default=12000, ge=1)

    # 整轮预算（模型调用、工具调用与纠错共用，重试不重置）
    max_model_calls: int = Field(default=3, ge=1, le=6)
    max_tool_calls: int = Field(default=8, ge=1, le=12)
    max_corrections: int = Field(default=1, ge=0, le=2)
    round_timeout_seconds: float = Field(default=45.0, gt=0)

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", populate_by_name=True)

    @model_validator(mode="after")
    def validate_model_mode(self):
        if self.policy_runtime_mode == "fixture":
            if not self.allow_fixture_model:
                raise ValueError(
                    "fixture 决策器必须显式设置 ALLOW_FIXTURE_MODEL=true，且只能用于开发测试"
                )
            if self.app_env not in ("dev", "test"):
                raise ValueError("生产环境禁止使用 fixture 决策器")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
