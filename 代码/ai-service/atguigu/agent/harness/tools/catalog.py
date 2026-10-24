from dataclasses import dataclass
from enum import StrEnum

from langchain_core.tools import BaseTool

from atguigu.agent.harness.tools.read import (
    get_invoice,
    get_order,
    get_plan,
    get_service_progress,
    list_after_sales,
    list_orders,
    search_knowledge,
    search_plans,
)
from atguigu.agent.harness.tools.skill import load_skill


class ToolCategory(StrEnum):
    """定义工具返回的数据类别。"""

    BUSINESS = "BUSINESS"
    KNOWLEDGE = "KNOWLEDGE"
    GUIDANCE = "GUIDANCE"


@dataclass(frozen=True)
class ToolDefinition:
    """组合一个 Agent 工具及其数据类别。"""

    tool: BaseTool
    category: ToolCategory


class ToolCatalog:
    """集中保存 Agent 可以使用的九个唯一工具。"""

    def __init__(self, definitions: tuple[ToolDefinition, ...]):
        self.definitions = {definition.tool.name: definition for definition in definitions}

    def get_agent_tools(self) -> list[BaseTool]:
        return [definition.tool for definition in self.definitions.values()]

    def get_tool_definition(self, tool_name: str) -> ToolDefinition:
        return self.definitions[tool_name]

    def get_tool_category(self, tool_name: str) -> ToolCategory:
        return self.get_tool_definition(tool_name).category

    def names(self) -> tuple[str, ...]:
        return tuple(self.definitions)


TOOL_CATALOG = ToolCatalog(
    (
        ToolDefinition(search_plans, ToolCategory.BUSINESS),
        ToolDefinition(get_plan, ToolCategory.BUSINESS),
        ToolDefinition(list_orders, ToolCategory.BUSINESS),
        ToolDefinition(get_order, ToolCategory.BUSINESS),
        ToolDefinition(get_invoice, ToolCategory.BUSINESS),
        ToolDefinition(get_service_progress, ToolCategory.BUSINESS),
        ToolDefinition(list_after_sales, ToolCategory.BUSINESS),
        ToolDefinition(search_knowledge, ToolCategory.KNOWLEDGE),
        ToolDefinition(load_skill, ToolCategory.GUIDANCE),
    )
)
