"""DeepSeek API 适配、真实连通性检查与结构化结果校验。"""

from dataclasses import dataclass
from typing import Any

import httpx
from langchain_deepseek import ChatDeepSeek

from atguigu.agent.llm.output import AgentOutput
from atguigu.common.config import get_settings
from atguigu.common.utils import get_utcnow

@dataclass
class ModelStatus:
    """当前推理部署的可观测状态。"""

    configured: bool
    reachable: bool
    model_name: str
    artifact_version: str
    runtime_mode: str
    detail: str
    checked_at: str


class ModelConfigurationError(RuntimeError):
    """模型地址、名称或密钥缺失。"""


class ModelAdapter:

    @classmethod
    def create_model(cls):
        """创建使用原生 tool_calls 的 DeepSeek 客户端。"""
        settings = get_settings()
        if settings.policy_runtime_mode == "fixture":
            # fixture 模式下不创建真实模型，避免误用外部地址
            raise ModelConfigurationError("fixture 模式不创建在线模型客户端")

        if not settings.llm_base_url or not settings.llm_model:
            raise ModelConfigurationError(
                "必须配置 LLM_BASE_URL 与 LLM_MODEL"
            )
        if not settings.llm_api_key:
            raise ModelConfigurationError("请配置 DEEPSEEK_API_KEY（或 LLM_API_KEY）")

        model_kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "api_key": settings.llm_api_key,
            "temperature": 0,
            "timeout": settings.llm_timeout_seconds,
        }

        return ChatDeepSeek(
            **model_kwargs,
            max_retries=0,
            extra_body={"thinking": {"type": settings.llm_thinking_mode}},
        )

    @classmethod
    async def check_deployment(cls) -> ModelStatus:
        """启动健康检查：确认推理服务可达且模型名称一致。"""
        settings = get_settings()
        checked_at = get_utcnow().isoformat()

        if settings.policy_runtime_mode == "fixture":
            return ModelStatus(
                configured=True,
                reachable=True,
                model_name="fixture-policy-v1",
                artifact_version="fixture-not-deployed",
                runtime_mode="FIXTURE",
                detail="开发测试替身，不是上线模型",
                checked_at=checked_at,
            )

        if not settings.llm_base_url or not settings.llm_model or not settings.llm_api_key:
            return ModelStatus(
                configured=False,
                reachable=False,
                model_name=settings.llm_model or "",
                artifact_version=settings.model_artifact_version,
                runtime_mode="DEEPSEEK",
                detail="未配置 DeepSeek 地址、模型名称或 API_KEY",
                checked_at=checked_at,
            )

        if not settings.llm_healthcheck_enabled:
            return ModelStatus(
                configured=True,
                reachable=True,
                model_name=settings.llm_model,
                artifact_version=settings.model_artifact_version,
                runtime_mode="DEEPSEEK",
                detail="已关闭健康检查",
                checked_at=checked_at,
            )

        url = f"{settings.llm_base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        try:
            async with httpx.AsyncClient(
                timeout=settings.llm_healthcheck_timeout_seconds, trust_env=False
            ) as client:
                response = await client.get(url, headers=headers)
            if response.status_code != 200:
                return ModelStatus(
                    configured=True,
                    reachable=False,
                    model_name=settings.llm_model,
                    artifact_version=settings.model_artifact_version,
                    runtime_mode="DEEPSEEK",
                    detail=f"健康检查返回 {response.status_code}",
                    checked_at=checked_at,
                )
            payload = response.json()
            served = [item.get("id") for item in payload.get("data", [])]
            matched = settings.llm_model in served
            return ModelStatus(
                configured=True,
                reachable=matched,
                model_name=settings.llm_model,
                artifact_version=settings.model_artifact_version,
                runtime_mode="DEEPSEEK",
                detail=(
                    "模型名称匹配"
                    if matched
                    else f"推理服务实际加载的模型为 {served}，与配置不一致"
                ),
                checked_at=checked_at,
            )
        except Exception as exc:  # noqa: BLE001
            return ModelStatus(
                configured=True,
                reachable=False,
                model_name=settings.llm_model,
                artifact_version=settings.model_artifact_version,
                runtime_mode="DEEPSEEK",
                detail=f"健康检查失败：{exc}",
                checked_at=checked_at,
            )

    @staticmethod
    def normalize_output(value: Any) -> AgentOutput:
        """从 Agent 执行结果中提取并校验结构化输出。"""
        structured_output = (
            value.get("structured_response") if isinstance(value, dict) else value
        )
        if isinstance(structured_output, AgentOutput):
            return structured_output
        return AgentOutput.model_validate(structured_output)
