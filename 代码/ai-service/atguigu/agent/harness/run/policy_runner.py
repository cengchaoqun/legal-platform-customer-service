import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from atguigu.agent.harness.errors import AgentExecutionError, TerminalErrorCode
from atguigu.agent.harness.run.fixture_policy import FixturePolicyPredictor
from atguigu.agent.llm.policy import PolicyDecision
from atguigu.agent.llm.policy_prompt import render_policy_prompt
from atguigu.app.schemas.run import AgentRunRequest


@dataclass
class PolicyPrediction:
    decision: PolicyDecision
    token_usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )
    runtime_mode: str = "deployed"


class PolicyPredictor(Protocol):
    async def predict(self, request: AgentRunRequest) -> PolicyPrediction: ...


class DeployedPolicyPredictor:
    """使用DeepSeek 模型完成 policy 任务。

    失败时抛出稳定错误码，由上层走固定兜底与人工入口；
    不会切换到任何外部商业聊天模型。
    """

    def __init__(self, agent: Any):
        self.agent = agent

    async def predict(self, request: AgentRunRequest) -> PolicyPrediction:
        callback = UsageMetadataCallbackHandler()
        try:
            result = await self.agent.ainvoke(
                {
                    "messages": [
                        SystemMessage(content=render_policy_prompt()),
                        HumanMessage(content=self._render_context(request)),
                    ]
                },
                config={"callbacks": [callback]},
            )
        except Exception as exc:  # noqa: BLE001
            raise AgentExecutionError(
                TerminalErrorCode.MODEL_CALL_FAILED, "模型决策调用失败"
            ) from exc

        structured = (
            result.get("structured_response") if isinstance(result, dict) else result
        )
        try:
            decision = (
                structured
                if isinstance(structured, PolicyDecision)
                else PolicyDecision.model_validate(structured)
            )
        except ValidationError as exc:
            raise AgentExecutionError(
                TerminalErrorCode.MODEL_OUTPUT_INVALID, "模型决策结构不符合约定"
            ) from exc

        usage = self._sum_usage(callback)
        return PolicyPrediction(decision=decision, token_usage=usage, runtime_mode="deepseek")

    @staticmethod
    def _render_context(request: AgentRunRequest) -> str:
        flow_payload = (
            request.flow_state.model_dump(mode="json") if request.flow_state else None
        )
        history = [
            {
                "role": message.role,
                "type": message.type,
                "content": message.content,
            }
            for message in request.history[-12:]
        ]
        current = [
            {
                "message_id": message.message_id,
                "type": message.type,
                "content": message.content,
            }
            for message in request.messages
        ]
        return (
            f"【合同版本】{request.contract_version}\n"
            f"【输入版本】{request.input_revision}\n"
            f"【流程状态版本】{request.flow_state_version}\n"
            f"【当前流程状态】\n{json.dumps(flow_payload, ensure_ascii=False, indent=2)}\n"
            f"【最近历史】\n{json.dumps(history, ensure_ascii=False)}\n"
            f"【本轮用户消息】\n{json.dumps(current, ensure_ascii=False)}"
        )

    @staticmethod
    def _sum_usage(callback: UsageMetadataCallbackHandler) -> dict[str, int]:
        items = list(callback.usage_metadata.values())
        return {
            "input_tokens": sum(item.get("input_tokens", 0) for item in items),
            "output_tokens": sum(item.get("output_tokens", 0) for item in items),
        }


class FixturePolicyPredictorAdapter:
    """开发测试用的离线决策器包装。"""

    def __init__(self):
        self._predictor = FixturePolicyPredictor()

    async def predict(self, request: AgentRunRequest) -> PolicyPrediction:
        decision = await self._predictor.predict(request)
        return PolicyPrediction(
            decision=decision,
            token_usage={"input_tokens": 0, "output_tokens": 0},
            runtime_mode="FIXTURE",
        )
