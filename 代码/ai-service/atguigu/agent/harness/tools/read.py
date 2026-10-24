from typing import Annotated

from langchain.tools import ToolRuntime, tool

from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.service import TOOL_SERVICE


def _call_id(runtime: ToolRuntime[AgentRuntimeContext], tool_label: str) -> str:
    if runtime.tool_call_id is None:
        raise RuntimeError(f"{tool_label}缺少调用编号")
    return runtime.tool_call_id


@tool
async def search_plans(
    query: Annotated[str, "用户描述的服务需求、套餐名称或服务类型关键词"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """检索当前在售的法律服务套餐。

    用户询问套餐、价格、权益、服务范围，但没有明确套餐编号时调用。
    返回套餐编号、名称、价格、权益摘要、有效期和服务类型。
    """
    return await TOOL_SERVICE.search_plans(
        runtime.context, _call_id(runtime, "套餐检索工具"), query
    )


@tool
async def get_plan(
    plan_id: Annotated[str, "套餐编号，例如 PLAN_CONTRACT_REVIEW"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """查询一个法律服务套餐的详细权益与适用范围。

    用户已经明确套餐编号或名称对应到唯一套餐时调用。
    """
    return await TOOL_SERVICE.get_plan(
        runtime.context, _call_id(runtime, "套餐详情工具"), plan_id
    )


@tool
async def list_orders(
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """查询当前用户的订单列表。

    用户询问订单但没有明确订单编号时调用；只返回当前登录用户自己的订单。
    返回订单编号、服务名称、金额、支付状态、履约状态与可用操作。
    """
    return await TOOL_SERVICE.list_orders(
        runtime.context, _call_id(runtime, "订单列表工具")
    )


@tool
async def get_order(
    order_id: Annotated[str, "订单编号，例如 ORDER_001"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """查询当前用户的一笔订单详情。

    用户已明确订单编号，或订单列表已唯一确定订单时调用。
    只读，不取消订单、不退款、不开票。
    """
    return await TOOL_SERVICE.get_order(
        runtime.context, _call_id(runtime, "订单详情工具"), order_id
    )


@tool
async def get_invoice(
    order_id: Annotated[str, "订单编号，例如 ORDER_001"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """查询一笔订单的开票状态。

    用户询问发票是否开具、开票进度或开票失败原因时调用。
    返回状态码与中文状态，不返回完整税号、邮箱等敏感资料。
    """
    return await TOOL_SERVICE.get_invoice(
        runtime.context, _call_id(runtime, "开票查询工具"), order_id
    )


@tool
async def get_service_progress(
    order_id: Annotated[str, "订单编号，例如 ORDER_001"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """查询一笔订单的法律服务办理进度。

    用户询问合同审核、法律咨询、文书起草进展，或缺少哪些材料时调用。
    返回当前节点、节点时间、缺失材料与业务方提供的预计时间（可能为空）。
    """
    return await TOOL_SERVICE.get_service_progress(
        runtime.context, _call_id(runtime, "办理进度工具"), order_id
    )


@tool
async def list_after_sales(
    runtime: ToolRuntime[AgentRuntimeContext],
    order_id: Annotated[str | None, "可选订单编号；提供后只查询该订单的售后记录"] = None,
) -> str:
    """查询当前用户的退款、取消或投诉申请记录。

    用户询问退款进度、审核结果、退款是否到账时调用。
    申请状态与退款到账状态分别返回。
    """
    return await TOOL_SERVICE.list_after_sales(
        runtime.context, _call_id(runtime, "售后记录工具"), order_id
    )


@tool
async def search_knowledge(
    query: Annotated[str, "需要查询的平台服务政策、规则或处理说明"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """检索版本化的平台服务政策。

    用户询问开票规则、退款条件、材料提交、服务时段等问题时调用。
    返回带知识编号、适用套餐、版本与生效时间的条目；没有匹配时返回空列表。
    """
    return await TOOL_SERVICE.search_knowledge(
        runtime.context, _call_id(runtime, "政策检索工具"), query
    )
