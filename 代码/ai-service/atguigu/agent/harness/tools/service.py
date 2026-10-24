"""九个工具的业务实现。

LangChain 工具包装与离线 fixture 执行器共用这里的实现，
确保工具契约、审计与结果校验在两条路径上完全一致。
"""

from typing import Any
from urllib.parse import quote

from atguigu.agent.harness.knowledge.output import KnowledgeItem
from atguigu.agent.harness.knowledge.service import KnowledgeQueryService
from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.executor import ToolExecutor
from atguigu.agent.harness.tools.output import (
    AfterSaleData,
    InvoiceData,
    OrderData,
    PlanData,
    PlanDetailData,
    ServiceProgressData,
    ToolResult,
)
from atguigu.infrastructure.client import LegalPlatformClient

TOOL_SEARCH_PLANS = "search_plans"
TOOL_GET_PLAN = "get_plan"
TOOL_LIST_ORDERS = "list_orders"
TOOL_GET_ORDER = "get_order"
TOOL_GET_INVOICE = "get_invoice"
TOOL_GET_SERVICE_PROGRESS = "get_service_progress"
TOOL_LIST_AFTER_SALES = "list_after_sales"
TOOL_SEARCH_KNOWLEDGE = "search_knowledge"
TOOL_LOAD_SKILL = "load_skill"

BUSINESS_TOOL_NAMES = (
    TOOL_SEARCH_PLANS,
    TOOL_GET_PLAN,
    TOOL_LIST_ORDERS,
    TOOL_GET_ORDER,
    TOOL_GET_INVOICE,
    TOOL_GET_SERVICE_PROGRESS,
    TOOL_LIST_AFTER_SALES,
)
ALL_TOOL_NAMES = (*BUSINESS_TOOL_NAMES, TOOL_SEARCH_KNOWLEDGE, TOOL_LOAD_SKILL)


def normalize_resource_id(resource_id: str) -> str:
    """统一业务编号格式，避免模型输出大小写或连接符差异导致查空。"""
    return resource_id.strip().upper().replace("-", "_")


class LegalToolService:
    """按当前用户身份执行只读业务查询与政策检索。"""

    def __init__(
        self,
        executor: ToolExecutor,
        knowledge: KnowledgeQueryService | None = None,
    ):
        self.executor = executor
        self.knowledge = knowledge or KnowledgeQueryService()

    async def execute(
        self,
        context: AgentRuntimeContext,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        operation,
        output_schema: Any,
    ) -> str:
        return await self.executor.execute(
            runtime_context=context,
            tool_call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            operation=operation,
            output_schema=output_schema,
        )

    async def search_plans(
        self, context: AgentRuntimeContext, call_id: str, query: str
    ) -> str:
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_SEARCH_PLANS,
            {"query": query},
            lambda: client.get_json("/plans", query_params={"query": query}),
            list[PlanData],
        )

    async def get_plan(
        self, context: AgentRuntimeContext, call_id: str, plan_id: str
    ) -> str:
        normalized = normalize_resource_id(plan_id)
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_GET_PLAN,
            {"plan_id": normalized},
            lambda: client.get_json(f"/plans/{quote(normalized, safe='')}"),
            PlanDetailData,
        )

    async def list_orders(self, context: AgentRuntimeContext, call_id: str) -> str:
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_LIST_ORDERS,
            {},
            lambda: client.get_json("/orders"),
            list[OrderData],
        )

    async def get_order(
        self, context: AgentRuntimeContext, call_id: str, order_id: str
    ) -> str:
        normalized = normalize_resource_id(order_id)
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_GET_ORDER,
            {"order_id": normalized},
            lambda: client.get_json(f"/orders/{quote(normalized, safe='')}"),
            OrderData,
        )

    async def get_invoice(
        self, context: AgentRuntimeContext, call_id: str, order_id: str
    ) -> str:
        normalized = normalize_resource_id(order_id)
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_GET_INVOICE,
            {"order_id": normalized},
            lambda: client.get_json(f"/orders/{quote(normalized, safe='')}/invoice"),
            InvoiceData,
        )

    async def get_service_progress(
        self, context: AgentRuntimeContext, call_id: str, order_id: str
    ) -> str:
        normalized = normalize_resource_id(order_id)
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_GET_SERVICE_PROGRESS,
            {"order_id": normalized},
            lambda: client.get_json(
                f"/orders/{quote(normalized, safe='')}/service-progress"
            ),
            ServiceProgressData,
        )

    async def list_after_sales(
        self, context: AgentRuntimeContext, call_id: str, order_id: str | None
    ) -> str:
        normalized = (
            normalize_resource_id(order_id) if order_id is not None else None
        )
        params = {"order_id": normalized} if normalized else None
        client = LegalPlatformClient(context.access_token)
        return await self.execute(
            context,
            call_id,
            TOOL_LIST_AFTER_SALES,
            params or {},
            lambda: client.get_json("/after-sales", query_params=params),
            list[AfterSaleData],
        )

    async def search_knowledge(
        self,
        context: AgentRuntimeContext,
        call_id: str,
        query: str,
        plan_id: str | None = None,
    ) -> str:
        normalized_query = query.strip()

        async def operation() -> object:
            items = await self.knowledge.search(normalized_query, plan_id=plan_id)
            return ToolResult[list[KnowledgeItem]](
                success=True,
                code="KNOWLEDGE_SEARCH_COMPLETED",
                message="已找到相关知识内容" if items else "未找到相关知识内容",
                data=items,
            ).model_dump(mode="python")

        return await self.execute(
            context,
            call_id,
            TOOL_SEARCH_KNOWLEDGE,
            {"query": normalized_query, **({"plan_id": plan_id} if plan_id else {})},
            operation,
            list[KnowledgeItem],
        )


TOOL_SERVICE = LegalToolService(executor=ToolExecutor())
