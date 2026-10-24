import json
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from pydantic import TypeAdapter, ValidationError

from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.output import (
    ToolFailureCode,
    ToolFailureType,
    ToolResult,
)
from atguigu.app.repositories.tool import ToolCallRepository
from atguigu.infrastructure.client import BusinessRejection
from atguigu.infrastructure.db import session_factory
from atguigu.models.models import AgentToolCall

ToolOperation = Callable[[], Awaitable[Any]]
logger = logging.getLogger(__name__)


class ToolExecutor:
    """统一记录、授权校验并校验 Agent 业务工具调用。

    三层校验：
    1. 外层 ToolResult 协议；
    2. success 与业务/服务/契约失败分类；
    3. data 的领域 Schema。
    HTTP 200 不等于业务成功。
    """

    def __init__(self):
        self.session_factory = session_factory

    async def execute(
        self,
        runtime_context: AgentRuntimeContext,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        operation: ToolOperation,
        output_schema: Any,
    ) -> str:
        # 1. 流程白名单校验：即使模型伪造调用，也不能越过当前节点
        if (
            runtime_context.allowed_tool_names
            and tool_name not in runtime_context.allowed_tool_names
        ):
            return await self._reject(
                runtime_context,
                tool_call_id,
                tool_name,
                arguments,
                "TOOL_NOT_ALLOWED",
                f"当前流程节点不允许调用 {tool_name}",
            )

        # 2. 整轮工具预算：纠错与重试不重置计数
        if not runtime_context.tool_budget.consume():
            return await self._reject(
                runtime_context,
                tool_call_id,
                tool_name,
                arguments,
                "TOOL_BUDGET_EXCEEDED",
                "本次处理可用工具次数已用完",
            )

        # 3. 先落审计记录，再执行
        await self._create_tool_call(runtime_context, tool_call_id, tool_name, arguments)

        started_at = perf_counter()
        try:
            response_data = await operation()
        except BusinessRejection as exc:
            # 业务拒绝：不盲目重试，交由模型向用户解释
            tool_result = ToolResult[Any](
                success=False,
                code=exc.code,
                message=exc.message,
                failure_type=ToolFailureType.BUSINESS,
            )
        except Exception:
            logger.exception("业务工具调用失败 [%s]", tool_name)
            tool_result = ToolResult[Any](
                success=False,
                code=ToolFailureCode.TOOL_CALL_FAILED,
                message="暂时无法取得可靠的业务数据",
                failure_type=ToolFailureType.SERVICE_CALL,
            )
        else:
            tool_result = self._validate_result(response_data, output_schema)

        latency_ms = int((perf_counter() - started_at) * 1000)
        await self._save_tool_result(
            runtime_context.run_id, tool_call_id, tool_result, latency_ms
        )
        return json.dumps(
            tool_result.model_dump(mode="json"), ensure_ascii=False
        )

    async def _reject(
        self,
        runtime_context: AgentRuntimeContext,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        code: str,
        message: str,
    ) -> str:
        """记录被拒绝的调用，返回模型可读的失败结果。"""
        tool_result = ToolResult[Any](
            success=False,
            code=code,
            message=message,
            failure_type=ToolFailureType.CONTRACT,
        )
        await self._create_tool_call(runtime_context, tool_call_id, tool_name, arguments)
        await self._save_tool_result(runtime_context.run_id, tool_call_id, tool_result, 0)
        return json.dumps(tool_result.model_dump(mode="json"), ensure_ascii=False)

    async def _create_tool_call(
        self,
        runtime_context: AgentRuntimeContext,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        async with self.session_factory() as session:
            repository = ToolCallRepository(session)
            repository.add(
                AgentToolCall(
                    run_id=runtime_context.run_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )
            await session.commit()

    async def _save_tool_result(
        self,
        run_id: str,
        tool_call_id: str,
        tool_result: ToolResult[Any],
        latency_ms: int,
    ):
        async with self.session_factory() as session:
            repository = ToolCallRepository(session)
            tool_call = await repository.find_by_run_and_call_id(run_id, tool_call_id)
            if tool_call is None:
                return
            tool_call.result = tool_result.model_dump(mode="json")
            tool_call.success = tool_result.success
            tool_call.latency_ms = latency_ms
            await session.commit()

    @staticmethod
    def _validate_result(response_data: Any, output_schema: Any) -> ToolResult[Any]:
        """校验统一结果结构和成功结果中的业务数据。"""
        try:
            tool_result = ToolResult[Any].model_validate(response_data)
            if not tool_result.success:
                return tool_result.model_copy(
                    update={"failure_type": ToolFailureType.BUSINESS}
                )

            data_adapter = TypeAdapter(output_schema)
            validated_data = data_adapter.validate_python(tool_result.data)
            return tool_result.model_copy(
                update={
                    "data": data_adapter.dump_python(validated_data, mode="json"),
                    "failure_type": None,
                }
            )
        except ValidationError:
            return ToolResult[Any](
                success=False,
                code=ToolFailureCode.INVALID_TOOL_RESULT,
                message="业务工具返回的数据不符合约定",
                failure_type=ToolFailureType.CONTRACT,
            )


TOOL_EXECUTOR = ToolExecutor()
