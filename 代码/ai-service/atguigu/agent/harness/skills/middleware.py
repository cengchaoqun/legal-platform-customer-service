from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from atguigu.agent.harness.run.runtime import AgentExecutionState, AgentRuntimeContext
from atguigu.agent.harness.skills.catalog import SKILL_CATALOG
from atguigu.agent.llm.output import AgentOutput


class SkillScopeMiddleware(
    AgentMiddleware[AgentExecutionState, AgentRuntimeContext, AgentOutput]
):
    """限制模型可见的工具。

    优先级：
    1. 有执行计划时，以计划白名单为准；load_skill 只提供说明，不能扩大工具范围；
    2. 没有计划时沿用 Skill 范围（兼容旧链路与单元测试）。
    """

    async def awrap_model_call(
        self,
        request: ModelRequest[AgentRuntimeContext],
        handler: Callable[
            [ModelRequest[AgentRuntimeContext]], Awaitable[ModelResponse[AgentOutput]]
        ],
    ) -> ModelResponse[AgentOutput]:
        plan_tools = request.state.get("allowed_tool_names")
        if plan_tools is not None:
            allowed_tool_names = {"load_skill", *plan_tools}
        else:
            active_skill_code = request.state.get("active_skill_code")
            if active_skill_code is None:
                allowed_tool_names = {"load_skill"}
            else:
                skill = SKILL_CATALOG.get_skill(active_skill_code)
                allowed_tool_names = {"load_skill", *skill.tools}

        scoped_request = request.override(
            tools=[tool for tool in request.tools if tool.name in allowed_tool_names],
            model_settings={
                **request.model_settings,
                "parallel_tool_calls": False,
            },
        )
        return await handler(scoped_request)
