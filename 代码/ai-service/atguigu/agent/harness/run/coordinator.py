import hashlib
import json
import logging
from time import perf_counter
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.agent.harness.errors import AgentExecutionError, TerminalErrorCode
from atguigu.agent.harness.run.events import build_response_event
from atguigu.agent.harness.run.executor import AgentExecutor
from atguigu.agent.harness.run.output import AgentRunOutPutMapper, ValidationSummaryBuilder
from atguigu.agent.harness.run.runtime import AgentRuntimeContext, ToolBudget
from atguigu.app.repositories.run import AgentRunRepository
from atguigu.app.schemas.run import AgentRunRequest, CustomerOutcomeRequest
from atguigu.common.config import get_settings
from atguigu.common.utils import get_utcnow
from atguigu.models.models import AgentRun, AgentRunState

logger = logging.getLogger(__name__)

FINAL_STATES = {
    AgentRunState.COMPLETED,
    AgentRunState.HANDED_OFF,
    AgentRunState.FAILED,
    AgentRunState.SUPERSEDED,
}


class AgentRunCoordinator:
    """Run 生命周期：幂等创建、执行、commit/cancel、接纳回执。"""

    def __init__(self, session: AsyncSession, executor: AgentExecutor):
        self.setting = get_settings()
        self.session = session
        self.executor = executor
        self.agent_run_repo = AgentRunRepository(session)

    async def start_run(
        self, user_id: str, access_token: str, request: AgentRunRequest
    ) -> dict:
        # 1. 契约与身份校验
        if request.contract_version != self.setting.contract_version:
            raise HTTPException(
                status_code=409,
                detail={"code": "CONTRACT_VERSION_MISMATCH", "message": "请求合同版本与服务不一致"},
            )
        if request.user_id != user_id:
            raise HTTPException(
                status_code=403,
                detail={"code": "PRINCIPAL_MISMATCH", "message": "请求用户与令牌主体不一致"},
            )

        fingerprint = self._fingerprint(request)

        # 2. 幂等：同一 request_id 返回原结果，不重复执行
        existing = await self.agent_run_repo.find_by_request_id(request.request_id)
        if existing is not None:
            self._assert_same_request(existing, user_id, fingerprint)
            if existing.state == AgentRunState.RUNNING:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "RUN_IN_PROGRESS", "message": "该请求正在处理中"},
                    headers={"Retry-After": "1"},
                )
            return build_response_event(existing)

        # 3. 短事务创建 RUNNING，模型与工具调用期间不持有数据库锁
        run = AgentRun(
            request_id=request.request_id,
            input_fingerprint=fingerprint,
            contract_version=request.contract_version,
            conversation_id=request.conversation_id,
            user_id=user_id,
            turn_id=request.turn_id,
            input_revision=request.input_revision,
            flow_state_version=request.flow_state_version,
            flow_definition_version=self.setting.flow_definition_version,
            state=AgentRunState.RUNNING,
            model_name=self.setting.llm_model or "fixture-policy-v1",
            model_artifact_version=self.setting.model_artifact_version,
            adapter_version=self.setting.adapter_version,
            runtime_mode="FIXTURE" if self.setting.policy_runtime_mode == "fixture" else "DEEPSEEK",
            prompt_version=f"{self.setting.policy_prompt_version}+{self.setting.execution_prompt_version}",
            input_context=self._sanitized_input(request),
        )
        self.agent_run_repo.add_agent_run(run)
        try:
            await self.session.commit()
        except IntegrityError:
            # 并发同 request_id：另一个请求已创建，交给幂等分支
            await self.session.rollback()
            existing = await self.agent_run_repo.find_by_request_id(request.request_id)
            if existing is None:
                raise
            raise HTTPException(
                status_code=409,
                detail={"code": "RUN_IN_PROGRESS", "message": "该请求正在处理中"},
                headers={"Retry-After": "1"},
            ) from None

        # 4. 锁外执行
        start_time = perf_counter()
        runtime_context = AgentRuntimeContext(
            run_id=run.id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            access_token=access_token,
            allowed_tool_names=frozenset(),
            tool_budget=ToolBudget(limit=self.setting.max_tool_calls),
        )
        try:
            outcome = await self.executor.execute(request, runtime_context)
            run.state, run.result = AgentRunOutPutMapper.map(outcome)
            run.input_tokens = outcome.token_usage.get("input_tokens", 0)
            run.output_tokens = outcome.token_usage.get("output_tokens", 0)
            run.policy_decision = outcome.decision.model_dump(mode="json")
            run.execution_plan = outcome.plan.to_prompt_payload()
            run.validation_summary = ValidationSummaryBuilder.build(outcome)
            run.runtime_mode = outcome.runtime_mode
        except Exception as exc:  # noqa: BLE001
            error_code = (
                exc.code.value if isinstance(exc, AgentExecutionError) else TerminalErrorCode.AGENT_EXECUTION_FAILED.value
            )
            logger.exception("Agent 执行失败 [%s] run_id=%s", error_code, run.id)
            run.state = AgentRunState.FAILED
            run.error = str(exc)
            run.result = {"code": error_code, "message": "AI Service 处理失败"}
            run.validation_summary = {"passed": False, "error_code": error_code}

        run.latency_ms = int((perf_counter() - start_time) * 1000)
        if run.state != AgentRunState.DECISION_PREPARED:
            run.finished_at = get_utcnow()
        await self.session.commit()
        return build_response_event(run)

    async def commit_run(
        self, user_id: str, run_id: str, input_revision: int
    ) -> dict:
        """接纳 AI 准备的页面动作结果。

        该操作只改变 AI 审计状态；不代表用户已提交申请或退款到账。
        """
        run = await self._require_run(user_id, run_id, lock=True)

        if run.state == AgentRunState.COMPLETED:
            return build_response_event(run)
        if run.state != AgentRunState.DECISION_PREPARED:
            raise HTTPException(
                status_code=409,
                detail={"code": "INVALID_RUN_STATE", "message": f"当前状态 {run.state} 不能提交"},
            )
        if run.input_revision != input_revision:
            raise HTTPException(
                status_code=409,
                detail={"code": "INPUT_SUPERSEDED", "message": "输入版本已变化，不能提交旧结果"},
            )

        run.state = AgentRunState.COMPLETED
        run.finished_at = get_utcnow()
        await self.session.commit()
        return build_response_event(run)

    async def cancel_run(self, user_id: str, run_id: str) -> None:
        run = await self._require_run(user_id, run_id, lock=True)

        if run.state == AgentRunState.SUPERSEDED:
            return
        if run.state != AgentRunState.DECISION_PREPARED:
            # 已接纳的结果不能被取消改写
            raise HTTPException(
                status_code=409,
                detail={"code": "INVALID_RUN_STATE", "message": f"当前状态 {run.state} 不能取消"},
            )
        run.state = AgentRunState.SUPERSEDED
        run.finished_at = get_utcnow()
        await self.session.commit()

    async def record_customer_outcome(
        self, user_id: str, run_id: str, payload: CustomerOutcomeRequest
    ) -> dict[str, Any]:
        """幂等记录 Customer 是否最终接纳。延迟回执时管理端显示待确认。"""
        run = await self._require_run(user_id, run_id, lock=True)

        if run.turn_id != payload.turn_id or run.input_revision != payload.input_revision:
            raise HTTPException(
                status_code=409,
                detail={"code": "OUTCOME_MISMATCH", "message": "回执关联的 Turn 或输入版本不一致"},
            )
        if run.customer_outcome == payload.outcome:
            return {"run_id": run.id, "customer_outcome": run.customer_outcome}
        if run.customer_outcome in ("ACCEPTED", "REJECTED"):
            raise HTTPException(
                status_code=409,
                detail={"code": "OUTCOME_CONFLICT", "message": "已有不同的接纳结论，不能覆盖"},
            )

        run.customer_outcome = payload.outcome
        run.outcome_reason = payload.reason
        run.outcome_recorded_at = get_utcnow()
        await self.session.commit()
        return {"run_id": run.id, "customer_outcome": run.customer_outcome}

    # ------------------------------------------------------------------ 内部
    async def _require_run(self, user_id: str, run_id: str, *, lock: bool) -> AgentRun:
        run = (
            await self.agent_run_repo.lock_by_id(run_id)
            if lock
            else await self.agent_run_repo.find_by_id(run_id)
        )
        if run is None:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run 不存在"})
        if run.user_id != user_id:
            raise HTTPException(status_code=403, detail={"code": "RUN_FORBIDDEN", "message": "无权访问该 Run"})
        return run

    @staticmethod
    def _assert_same_request(run: AgentRun, user_id: str, fingerprint: str) -> None:
        if run.user_id != user_id or run.input_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REQUEST_ID_CONFLICT",
                    "message": "同一 request_id 被用于不同用户或不同输入",
                },
            )

    @staticmethod
    def _fingerprint(request: AgentRunRequest) -> str:
        payload = {
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "user_id": request.user_id,
            "input_revision": request.input_revision,
            "flow_state_version": request.flow_state_version,
            "messages": [
                {"message_id": m.message_id, "type": m.type, "content": m.content}
                for m in request.messages
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _sanitized_input(request: AgentRunRequest) -> dict[str, Any]:
        """审计输入不保存完整历史与任何令牌。"""
        return {
            "contract_version": request.contract_version,
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "user_id": request.user_id,
            "input_revision": request.input_revision,
            "flow_state_version": request.flow_state_version,
            "messages": [
                {"message_id": m.message_id, "type": m.type, "content": m.content}
                for m in request.messages
            ],
            "history_count": len(request.history),
        }
