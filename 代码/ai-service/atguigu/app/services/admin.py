from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.models.models import AgentRun, AgentToolCall

SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "token",
    "api_key",
    "tax_id",
    "email",
    "phone",
    "id_card",
}


def _mask(value: Any) -> Any:
    """工具参数/结果按权限脱敏：不返回令牌与完整敏感资料。"""
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in SENSITIVE_KEYS else _mask(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask(item) for item in value[:20]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


class AdminService:
    """管理员观测 AI Run 的查询能力。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_metrics(self) -> dict:
        agent_runs = await self.session.scalar(select(func.count()).select_from(AgentRun))
        average_latency_ms = await self.session.scalar(select(func.avg(AgentRun.latency_ms)))
        input_tokens = await self.session.scalar(select(func.sum(AgentRun.input_tokens)))
        output_tokens = await self.session.scalar(select(func.sum(AgentRun.output_tokens)))

        # 注意：JSON 表达式必须只构造一次，否则 SELECT 与 GROUP BY 会生成
        # 不同的绑定参数，PostgreSQL 无法将其识别为同一分组表达式。
        flow_type_expr = AgentRun.execution_plan["flow_type"].as_string()
        by_flow = (
            await self.session.execute(
                select(
                    flow_type_expr,
                    func.count(),
                )
                .group_by(flow_type_expr)
            )
        ).all()
        by_outcome = (
            await self.session.execute(
                select(AgentRun.customer_outcome, func.count()).group_by(AgentRun.customer_outcome)
            )
        ).all()
        by_runtime = (
            await self.session.execute(
                select(AgentRun.runtime_mode, func.count()).group_by(AgentRun.runtime_mode)
            )
        ).all()

        return {
            "agent_runs": agent_runs or 0,
            "average_latency_ms": average_latency_ms,
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "by_flow": {str(key): count for key, count in by_flow},
            "by_customer_outcome": {str(key): count for key, count in by_outcome},
            "by_runtime_mode": {str(key): count for key, count in by_runtime},
        }

    async def list_runs(self, limit: int, state: str | None = None) -> list[dict]:
        statement = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(min(limit, 200))
        if state:
            statement = statement.where(AgentRun.state == state)
        runs = (await self.session.scalars(statement)).all()
        return [
            {
                "id": run.id,
                "request_id": run.request_id,
                "conversation_id": run.conversation_id,
                "turn_id": run.turn_id,
                "state": run.state,
                "model_name": run.model_name,
                "model_artifact_version": run.model_artifact_version,
                "runtime_mode": run.runtime_mode,
                "contract_version": run.contract_version,
                "flow_definition_version": run.flow_definition_version,
                "flow_type": (run.execution_plan or {}).get("flow_type"),
                "flow_node": (run.execution_plan or {}).get("node"),
                "input_revision": run.input_revision,
                "flow_state_version": run.flow_state_version,
                "customer_outcome": run.customer_outcome,
                "outcome_reason": run.outcome_reason,
                "latency_ms": run.latency_ms,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "started_at": run.started_at.isoformat() if run.started_at else None,
            }
            for run in runs
        ]

    async def get_run(self, run_id: str) -> dict:
        run = await self.session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run 不存在")
        tool_calls = (
            await self.session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.run_id == run_id)
                .order_by(AgentToolCall.created_at)
            )
        ).all()
        return {
            "run": {
                "id": run.id,
                "request_id": run.request_id,
                "conversation_id": run.conversation_id,
                "turn_id": run.turn_id,
                "state": run.state,
                "model_name": run.model_name,
                "model_artifact_version": run.model_artifact_version,
                "adapter_version": run.adapter_version,
                "runtime_mode": run.runtime_mode,
                "prompt_version": run.prompt_version,
                "contract_version": run.contract_version,
                "flow_definition_version": run.flow_definition_version,
                "input_revision": run.input_revision,
                "flow_state_version": run.flow_state_version,
                "policy_decision": run.policy_decision,
                "execution_plan": run.execution_plan,
                "validation_summary": run.validation_summary,
                "outcome": _mask(run.result),
                "customer_outcome": run.customer_outcome,
                "outcome_reason": run.outcome_reason,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "latency_ms": run.latency_ms,
                "error": run.error,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            },
            "tool_calls": [
                {
                    "tool_call_id": call.tool_call_id,
                    "tool_name": call.tool_name,
                    "success": call.success,
                    "arguments": _mask(call.arguments),
                    "result": _mask(call.result),
                    "latency_ms": call.latency_ms,
                }
                for call in tool_calls
            ],
        }
