import logging
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import SystemMessage
from pydantic import ValidationError

from atguigu.agent.harness.errors import (
    AgentExecutionError,
    AgentOutputValidationError,
    CorrectableErrorCode,
    TerminalErrorCode,
)
from atguigu.agent.harness.flows.contract import FlowSnapshot
from atguigu.agent.harness.flows.planner import ExecutionPlanner
from atguigu.agent.harness.flows.reducer import EvidenceCollector, FlowReducer
from atguigu.agent.harness.run.context import ContextCompiler
from atguigu.agent.harness.run.fixture_agent import FixtureAgentRunner, build_direct_output
from atguigu.agent.harness.run.policy_runner import PolicyPrediction, PolicyPredictor
from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.snapshot import ToolCallReader
from atguigu.agent.harness.validator.output import AgentOutputValidator, ValidatedAgentOutput
from atguigu.agent.llm.adapter import ModelAdapter
from atguigu.agent.llm.output import AgentOutput, ReplyType
from atguigu.agent.llm.policy import ExecutionPlan, PolicyDecision
from atguigu.app.schemas.run import AgentRunRequest
from atguigu.common.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ExecutionOutcome:
    """一次 Run 的完整计算结果，供审计与事件映射使用。"""

    validated_output: ValidatedAgentOutput
    decision: PolicyDecision
    plan: ExecutionPlan
    patch: Any
    token_usage: dict[str, int]
    runtime_mode: str
    evidence: Any = None


class AgentExecutor:
    """Run 执行器：决策 → 计划 → 受控执行 → 校验 → 归约补丁。"""

    def __init__(
        self,
        policy_predictor: PolicyPredictor,
        agent_runner: Any | None,
        output_validator: AgentOutputValidator,
        tool_call_reader: ToolCallReader,
        fixture_runner: FixtureAgentRunner | None = None,
    ):
        self.settings = get_settings()
        self.policy_predictor = policy_predictor
        self.agent_runner = agent_runner
        self.output_validator = output_validator
        self.tool_call_reader = tool_call_reader
        self.fixture_runner = fixture_runner
        self.context_compiler = ContextCompiler()
        self.planner = ExecutionPlanner()
        self.reducer = FlowReducer()
        self.max_attempts = self.settings.max_corrections + 1

    async def execute(
        self, request: AgentRunRequest, runtime_context: AgentRuntimeContext
    ) -> ExecutionOutcome:
        # 1. 模型决策：只给意图、槽位候选与下一步建议
        prediction: PolicyPrediction = await self.policy_predictor.predict(request)

        # 2. 代码计算计划：节点、工具、缺失槽位、允许动作
        snapshot = request.flow_state or FlowSnapshot(
            flow_id="", node="INIT", state_version=0
        )
        plan = self.planner.build(prediction.decision, snapshot)
        logger.info(
            "Run %s 计划 flow=%s node=%s action=%s tools=%s",
            runtime_context.run_id,
            plan.flow_type,
            plan.node,
            plan.planned_action,
            plan.allowed_tool_names,
        )

        # 3. 执行：直接澄清/转人工跳过工具；其余走受控 Agent
        if plan.mode == "DIRECT":
            output = build_direct_output(prediction.decision, plan)
            validated = await self._validate(output, runtime_context, plan)
        else:
            validated = await self._execute_agent(
                request, runtime_context, plan, prediction.decision
            )

        # 4. 读取本次 Run 的工具证据，生成候选流程补丁
        snapshots = await self.tool_call_reader.read_by_run_id(runtime_context.run_id)
        evidence = EvidenceCollector.collect(snapshots)
        patch = self.reducer.reduce(
            snapshot,
            plan,
            prediction.decision,
            validated,
            evidence,
            run_id=runtime_context.run_id,
            turn_id=request.turn_id,
        )

        return ExecutionOutcome(
            validated_output=validated,
            decision=prediction.decision,
            plan=plan,
            patch=patch,
            token_usage=self._merge_usage(prediction.token_usage, validated.token_usage),
            runtime_mode=prediction.runtime_mode,
            evidence=evidence,
        )

    # ------------------------------------------------------------- 执行路径
    async def _execute_agent(
        self,
        request: AgentRunRequest,
        runtime_context: AgentRuntimeContext,
        plan: ExecutionPlan,
        decision: PolicyDecision,
    ) -> ValidatedAgentOutput:
        if self.settings.policy_runtime_mode == "fixture":
            if self.fixture_runner is None:
                raise AgentExecutionError(
                    TerminalErrorCode.AGENT_EXECUTION_FAILED, "fixture 执行器未初始化"
                )
            output = await self.fixture_runner.run(
                request, runtime_context, plan, decision
            )
            return await self._validate(output, runtime_context, plan)

        if self.agent_runner is None:
            raise AgentExecutionError(
                TerminalErrorCode.MODEL_CALL_FAILED, "DeepSeek 模型不可用"
            )

        messages = self.context_compiler.compile_messages(request)
        usage_callback = UsageMetadataCallbackHandler()
        attempt = 0
        while True:
            try:
                raw = await self.agent_runner.ainvoke(
                    {
                        "messages": messages,
                        "selected_skill": plan.selected_skill,
                        "allowed_tool_names": plan.allowed_tool_names,
                        "execution_plan": self._plan_payload(plan, decision),
                        "plan_node": plan.node,
                    },
                    context=runtime_context,
                    config={"callbacks": [usage_callback]},
                )
                output = ModelAdapter.normalize_output(raw)
                validated = await self._validate(output, runtime_context, plan)
                return validated.model_copy(
                    update={"token_usage": self._usage(usage_callback)}
                )
            except ToolCallLimitExceededError:
                return ValidatedAgentOutput(
                    reply_type=ReplyType.DECLINE,
                    reply_content="本次查询调用业务工具次数过多，暂时无法完成查询，请稍后重试或转人工。",
                    token_usage=self._usage(usage_callback),
                )
            except AgentOutputValidationError as exc:
                if attempt >= self.max_attempts - 1 or not isinstance(
                    exc.code, CorrectableErrorCode
                ):
                    raise AgentExecutionError(exc.code, str(exc)) from exc
                # 保留本轮轨迹并追加一次纠错反馈
                messages = [
                    *(raw.get("messages") or messages),
                    SystemMessage(
                        content=(
                            "上一次最终输出未通过服务端校验："
                            f"{exc}。请严格按执行计划重新生成；"
                            "只使用成功工具结果，不要声称已提交申请、已退款或已开票。"
                        )
                    ),
                ]
                attempt += 1
            except ValidationError as exc:
                raise AgentExecutionError(
                    TerminalErrorCode.MODEL_OUTPUT_INVALID, "模型输出结构不符合约定"
                ) from exc
            except AgentExecutionError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AgentExecutionError(
                    TerminalErrorCode.MODEL_CALL_FAILED, "模型调用失败"
                ) from exc

    async def _validate(
        self,
        output: AgentOutput,
        runtime_context: AgentRuntimeContext,
        plan: ExecutionPlan,
    ) -> ValidatedAgentOutput:
        return await self.output_validator.validate(
            output,
            runtime_context.run_id,
            allowed_next_actions=plan.allowed_next_actions,
            allowed_action_codes=plan.allowed_action_codes,
            clarify_slot=plan.clarify_slot,
            require_knowledge=plan.flow_type == "POLICY_QUERY",
        )

    @staticmethod
    def _plan_payload(plan: ExecutionPlan, decision: PolicyDecision) -> dict[str, Any]:
        payload = plan.to_prompt_payload()
        payload["primary_intent"] = decision.primary_intent.value
        payload["slot_candidates"] = {
            name: update.value for name, update in decision.slot_updates.items()
        }
        payload["conflicts"] = [item.model_dump() for item in decision.conflicts]
        return payload

    def _merge_usage(self, first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
        return {
            "input_tokens": int(first.get("input_tokens", 0)) + int(second.get("input_tokens", 0)),
            "output_tokens": int(first.get("output_tokens", 0)) + int(second.get("output_tokens", 0)),
        }

    @staticmethod
    def _usage(callback: UsageMetadataCallbackHandler) -> dict[str, int]:
        items = list(callback.usage_metadata.values())
        return {
            "input_tokens": sum(item.get("input_tokens", 0) for item in items),
            "output_tokens": sum(item.get("output_tokens", 0) for item in items),
        }
