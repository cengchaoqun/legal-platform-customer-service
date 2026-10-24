from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy

from atguigu.agent.harness.dynamic_prompt import support_prompt
from atguigu.agent.harness.run.runtime import AgentExecutionState, AgentRuntimeContext
from atguigu.agent.harness.skills.middleware import SkillScopeMiddleware
from atguigu.agent.harness.tools.catalog import TOOL_CATALOG
from atguigu.agent.llm.adapter import ModelAdapter
from atguigu.agent.llm.output import AgentOutput
from atguigu.agent.llm.policy import PolicyDecision
from atguigu.common.config import get_settings


def create_support_agent():
    """execution 任务：在计划限定的工具与动作范围内生成结构化回复。"""
    settings = get_settings()
    return create_agent(  # type:ignore
        model=ModelAdapter.create_model(),
        tools=TOOL_CATALOG.get_agent_tools(),
        response_format=ToolStrategy(AgentOutput),
        state_schema=AgentExecutionState,
        context_schema=AgentRuntimeContext,
        middleware=[
            support_prompt,
            SkillScopeMiddleware(),
            ToolCallLimitMiddleware(
                run_limit=settings.max_tool_calls,
                exit_behavior="error",
            ),
        ],
        name="法律平台智能客服",
    )


def create_policy_agent():
    """policy 任务：只输出 PolicyDecision，不调用业务工具。"""
    return create_agent(  # type:ignore
        model=ModelAdapter.create_model(),
        tools=[],
        response_format=ToolStrategy(PolicyDecision),
        name="法律平台流程决策",
    )
