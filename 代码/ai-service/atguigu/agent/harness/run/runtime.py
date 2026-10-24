from dataclasses import dataclass, field
from typing import Any

from langchain.agents import AgentState
from typing_extensions import NotRequired

from atguigu.agent.llm.output import AgentOutput


@dataclass
class ToolBudget:
    """整轮工具预算。

    对象在一次 Run 内共享；纠错重试不会重置计数。
    """

    limit: int
    used: int = 0

    def consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass(frozen=True)
class AgentRuntimeContext:
    """提供给 Agent 和业务工具的单次运行信息。

    `allowed_tool_names` 来自 ExecutionPlan；工具执行器会再校验一次，
    防止仅隐藏工具但仍能越过流程调用。
    """

    run_id: str
    conversation_id: str
    user_id: str
    access_token: str
    allowed_tool_names: frozenset[str] = frozenset()
    tool_budget: ToolBudget = field(default_factory=lambda: ToolBudget(limit=8))
    selected_skill: str | None = None
    plan_node: str = "INIT"


class AgentExecutionState(AgentState[AgentOutput]):  # type:ignore
    """Agent 多步骤执行期间可变的状态；不是跨轮持久化的 FlowState。"""

    active_skill_code: NotRequired[str]
    allowed_tool_names: NotRequired[list[str]]
    selected_skill: NotRequired[str]
    plan_node: NotRequired[str]
    # 只读的计划摘要，供动态提示渲染；预算与内部原因不进入提示
    execution_plan: NotRequired[dict[str, Any]]
