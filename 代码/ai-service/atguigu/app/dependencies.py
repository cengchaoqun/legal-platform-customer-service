from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.agent.harness.run.coordinator import AgentRunCoordinator
from atguigu.agent.harness.run.executor import AgentExecutor
from atguigu.agent.harness.tools.snapshot import ToolCallReader
from atguigu.agent.harness.validator.output import AgentOutputValidator
from atguigu.app.repositories.tool import ToolCallRepository
from atguigu.app.services.auth import AuthService
from atguigu.common.config import get_settings
from atguigu.infrastructure.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_service() -> AuthService:
    return AuthService()


def get_tool_call_reader(session: SessionDep) -> ToolCallReader:
    return ToolCallReader(ToolCallRepository(session))


ToolCallReaderDep = Annotated[ToolCallReader, Depends(get_tool_call_reader)]


def get_agent_output_validator(tool_call_reader: ToolCallReaderDep) -> AgentOutputValidator:
    return AgentOutputValidator(tool_call_reader)


AgentOutputValidatorDep = Annotated[AgentOutputValidator, Depends(get_agent_output_validator)]


def get_agent_executor(
    request: Request,
    session: SessionDep,
    output_validator: AgentOutputValidatorDep,
) -> AgentExecutor:
    """按请求组装执行器，避免把用户令牌放进共享实例。"""
    reader = ToolCallReader(ToolCallRepository(session))
    return AgentExecutor(
        policy_predictor=request.app.state.policy_predictor,
        agent_runner=request.app.state.agent_runner,
        output_validator=output_validator,
        tool_call_reader=reader,
        fixture_runner=request.app.state.fixture_runner,
    )


AgentExecutorDep = Annotated[AgentExecutor, Depends(get_agent_executor)]


def require_internal_service(
    token: Annotated[str | None, Header(alias="X-Internal-Service-Token")] = None,
) -> None:
    """只允许 Customer Service 调用内部接口。"""
    if token != get_settings().internal_service_token:
        raise HTTPException(status_code=403, detail="Invalid internal service token")


def get_agent_coordinator(session: SessionDep, executor: AgentExecutorDep) -> AgentRunCoordinator:
    return AgentRunCoordinator(session=session, executor=executor)


AgentRunCoordinatorDep = Annotated[AgentRunCoordinator, Depends(get_agent_coordinator)]
