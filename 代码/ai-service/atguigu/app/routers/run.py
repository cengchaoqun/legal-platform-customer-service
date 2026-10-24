from typing import Annotated

from fastapi import APIRouter, Depends, Header

from atguigu.app.dependencies import (
    AgentRunCoordinatorDep,
    get_auth_service,
    require_internal_service,
)
from atguigu.app.schemas.run import (
    AgentRunRequest,
    CommitRunRequest,
    CustomerOutcomeRequest,
)

router = APIRouter(prefix="/internal/v1/agent", tags=["AI Run"])


@router.post("/runs")
async def start_run(
    request: AgentRunRequest,
    _: Annotated[None, Depends(require_internal_service)],
    agent_coordinator: AgentRunCoordinatorDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """启动一次 Agent Run。

    同一 request_id 重试返回原事件；仍在运行返回 409 RUN_IN_PROGRESS。
    """
    auth_service = get_auth_service()
    current_user = auth_service.get_authorized_user(authorization, "customer")
    access_token = auth_service.get_bearer_token(authorization)
    return await agent_coordinator.start_run(current_user.user_id, access_token, request)


@router.post("/runs/{run_id}/commit")
async def commit_run(
    run_id: str,
    payload: CommitRunRequest,
    agent_coordinator: AgentRunCoordinatorDep,
    _: Annotated[None, Depends(require_internal_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """接纳 AI 准备的页面动作结果。"""
    current_user = get_auth_service().get_authorized_user(authorization, "customer")
    return await agent_coordinator.commit_run(
        current_user.user_id, run_id, payload.input_revision
    )


@router.post("/runs/{run_id}/confirm")
async def confirm_run_compat(
    run_id: str,
    payload: CommitRunRequest,
    agent_coordinator: AgentRunCoordinatorDep,
    _: Annotated[None, Depends(require_internal_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """兼容旧路径：与 /commit 共用同一状态机，兼容期结束后移除。"""
    current_user = get_auth_service().get_authorized_user(authorization, "customer")
    return await agent_coordinator.commit_run(
        current_user.user_id, run_id, payload.input_revision
    )


@router.post("/runs/{run_id}/cancel", status_code=204)
async def cancel_run(
    run_id: str,
    agent_coordinator: AgentRunCoordinatorDep,
    _: Annotated[None, Depends(require_internal_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """取消尚未接纳的 prepared 结果；已完成结果不会被改写。"""
    current_user = get_auth_service().get_authorized_user(authorization, "customer")
    await agent_coordinator.cancel_run(current_user.user_id, run_id)


@router.post("/runs/{run_id}/customer-outcome")
async def record_customer_outcome(
    run_id: str,
    payload: CustomerOutcomeRequest,
    agent_coordinator: AgentRunCoordinatorDep,
    _: Annotated[None, Depends(require_internal_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """记录 Customer 是否最终接纳该 Run，幂等。"""
    current_user = get_auth_service().get_authorized_user(authorization, "customer")
    return await agent_coordinator.record_customer_outcome(
        current_user.user_id, run_id, payload
    )
