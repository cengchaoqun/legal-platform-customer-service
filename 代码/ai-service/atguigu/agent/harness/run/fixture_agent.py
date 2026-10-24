"""离线执行器：用规则模板 + 真实工具结果生成 AgentOutput。

与在线 Agent 共用同一套工具、ToolExecutor、Schema 校验与事实校验，
用来在没有 GPU 时验证协议、流程、结算与并发。它不做自由文本生成，
因此不能替代真实模型的能力验收。
"""

import json
from dataclasses import dataclass
from typing import Any

from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.service import LegalToolService
from atguigu.agent.llm.output import (
    AgentOutput,
    ClarificationOption,
    HandoffReason,
    HandoffRequest,
    PageActionRequest,
    ReplyType,
)
from atguigu.agent.llm.policy import ExecutionPlan, PolicyDecision
from atguigu.app.schemas.run import AgentRunRequest

SERVICE_LABELS = {
    "PENDING_START": "待开始",
    "IN_SERVICE": "服务中",
    "WAITING_MATERIALS": "材料待补充",
    "DELIVERED": "已交付",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
}
PAYMENT_LABELS = {"UNPAID": "待支付", "PAID": "已支付", "REFUNDED": "已退款"}
INVOICE_LABELS = {
    "NONE": "尚未申请开票",
    "SUBMITTED": "开票申请已受理",
    "PROCESSING": "开票处理中",
    "ISSUED": "发票已开具",
    "FAILED": "开票失败",
}


@dataclass
class ResolvedOrder:
    order: dict[str, Any] | None = None
    output: AgentOutput | None = None


class FixtureAgentRunner:
    """规则驱动的 execution 执行器。"""

    def __init__(self, tool_service: LegalToolService):
        self.tools = tool_service
        self._counter = 0

    def _call_id(self, tool_name: str) -> str:
        self._counter += 1
        return f"fixture_{tool_name}_{self._counter}"

    async def _tool(self, coro) -> dict[str, Any]:
        raw = await coro
        return json.loads(raw)

    # ------------------------------------------------------------------ 入口
    async def run(
        self,
        request: AgentRunRequest,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
        decision: PolicyDecision,
    ) -> AgentOutput:
        flow_type = plan.flow_type
        if flow_type == "PLAN_QUERY":
            return await self._plan_query(request, context, decision)
        if flow_type == "POLICY_QUERY":
            return await self._policy_query(request, context)
        if flow_type == "AFTER_SALE_QUERY":
            return await self._after_sales_query(request, context, plan)

        resolved = await self._resolve_order(request, context, plan, decision)
        if resolved.output is not None:
            return resolved.output
        order = resolved.order or {}

        if flow_type == "REFUND_APPLY":
            return await self._refund(context, plan, decision, order, request)
        if flow_type == "INVOICE_APPLY":
            return await self._invoice_apply(context, plan, order)
        if flow_type == "CANCEL_ORDER":
            return await self._cancel(plan, order)
        if flow_type == "SERVICE_PROGRESS_QUERY":
            return await self._progress(context, plan, order)
        if flow_type == "INVOICE_QUERY":
            return await self._invoice_query(context, plan, order)
        return await self._order_query(plan, order)

    # ----------------------------------------------------------------- 订单
    async def _resolve_order(
        self,
        request: AgentRunRequest,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
        decision: PolicyDecision,
    ) -> ResolvedOrder:
        order_id = plan.target_resource.get("order_id")
        if not order_id and decision.slot_updates.get("order_id"):
            order_id = decision.slot_updates["order_id"].value
        if not order_id and request.flow_state and request.flow_state.slots.get("order_id"):
            order_id = request.flow_state.slots["order_id"].value

        if order_id:
            result = await self._tool(
                self.tools.get_order(context, self._call_id("get_order"), order_id)
            )
            if result.get("success"):
                return ResolvedOrder(order=result["data"])
            # 业务拒绝：交给校验器使用业务原话生成安全回复
            return ResolvedOrder(
                output=AgentOutput(
                    reply_type=ReplyType.ANSWER, reply_content=str(result.get("message"))
                )
            )

        result = await self._tool(self.tools.list_orders(context, self._call_id("list_orders")))
        if not result.get("success"):
            return ResolvedOrder(
                output=AgentOutput(
                    reply_type=ReplyType.ANSWER, reply_content=str(result.get("message"))
                )
            )
        orders = result.get("data") or []
        if len(orders) == 0:
            return ResolvedOrder(
                output=AgentOutput(
                    reply_type=ReplyType.ANSWER,
                    reply_content="当前账号下没有查到订单。你可以在“我的订单”页面确认是否使用了其他账号。",
                )
            )

        # 用户已经给出套餐线索时，只在对应套餐的订单里选择
        plan_id = plan.target_resource.get("plan_id")
        if not plan_id and decision.slot_updates.get("plan_id"):
            plan_id = decision.slot_updates["plan_id"].value
        if not plan_id and request.flow_state and request.flow_state.slots.get("plan_id"):
            plan_id = request.flow_state.slots["plan_id"].value
        if plan_id:
            filtered = [item for item in orders if item.get("plan_id") == plan_id]
            if filtered:
                orders = filtered

        if len(orders) > 1:
            options = [
                ClarificationOption(
                    object_type="order",
                    object_id=item["id"],
                    label=f"{item['id']} · {item['service_name']}",
                    description=(
                        f"{str(item.get('created_at', ''))[:10]} · "
                        f"{item.get('total_amount')} 元 · "
                        f"{SERVICE_LABELS.get(item['service_status'], item['service_status'])}"
                    ),
                )
                for item in orders[:5]
            ]
            return ResolvedOrder(
                output=AgentOutput(
                    reply_type=ReplyType.CLARIFY,
                    reply_content="查到你有两笔以上订单，请选择需要办理的那一笔？",
                    clarification_slot="order_id",
                    clarification_options=options,
                )
            )

        # 列表只返回概要，进入具体流程前再查一次详情，确保动作证据与字段完整
        chosen_id = orders[0]["id"]
        detail = await self._tool(
            self.tools.get_order(context, self._call_id("get_order"), chosen_id)
        )
        if detail.get("success"):
            return ResolvedOrder(order=detail["data"])
        return ResolvedOrder(
            output=AgentOutput(
                reply_type=ReplyType.ANSWER, reply_content=str(detail.get("message"))
            )
        )

    # ----------------------------------------------------------------- 退款
    async def _refund(
        self,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
        decision: PolicyDecision,
        order: dict[str, Any],
        request: AgentRunRequest,
    ) -> AgentOutput:
        order_id = order["id"]
        allowed = order.get("allowed_actions") or []
        reason = decision.slot_updates.get("refund_reason")
        reason_value = reason.value if reason else None
        if not reason_value and request.flow_state:
            existing = request.flow_state.slots.get("refund_reason")
            reason_value = existing.value if existing else None

        after_sales = await self._tool(
            self.tools.list_after_sales(context, self._call_id("list_after_sales"), order_id)
        )
        open_record = None
        if after_sales.get("success"):
            records = after_sales.get("data") or []
            open_record = next(
                (
                    item
                    for item in records
                    if item.get("application_status") in ("SUBMITTED", "REVIEWING")
                ),
                None,
            )

        if open_record is not None:
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    f"订单 {order_id} 已有一笔{open_record.get('kind_label', '售后')}在处理中"
                    f"（编号 {open_record['id']}），当前状态为"
                    f"{open_record['application_status_label']}，退款状态为"
                    f"{open_record['refund_status_label']}。申请状态与资金到账状态是两件不同的事。"
                ),
                page_action_request=PageActionRequest(
                    action_code="VIEW_AFTER_SALES", resource_id=order_id
                ),
            )

        if "APPLY_AFTER_SALE" not in allowed:
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    f"订单 {order_id} 当前服务状态为"
                    f"{order.get('service_status_label') or SERVICE_LABELS.get(order['service_status'])}，"
                    "目前不能直接提交退款申请。你可以在订单详情查看售后记录；"
                    "如对处理结果有异议，可以转人工客服核实。"
                ),
                page_action_request=PageActionRequest(
                    action_code="VIEW_AFTER_SALES", resource_id=order_id
                ),
            )

        if not reason_value:
            return AgentOutput(
                reply_type=ReplyType.CLARIFY,
                reply_content="这笔订单可以提交退款申请。方便说明一下申请退款的原因吗？",
                clarification_slot="refund_reason",
            )

        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=(
                f"订单 {order_id}（{order['service_name']}，实付 {order.get('total_amount')} 元）"
                "可以提交退款申请。请在申请页面确认订单与退款原因后提交；"
                "提交后进入人工审核，审核通过后才会进入退款处理。"
            ),
            page_action_request=PageActionRequest(
                action_code="APPLY_AFTER_SALE", resource_id=order_id
            ),
        )

    # ----------------------------------------------------------------- 开票
    async def _invoice_apply(
        self,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
        order: dict[str, Any],
    ) -> AgentOutput:
        order_id = order["id"]
        allowed = order.get("allowed_actions") or []
        invoice = await self._tool(
            self.tools.get_invoice(context, self._call_id("get_invoice"), order_id)
        )
        invoice_data = invoice.get("data") if invoice.get("success") else None
        status = (invoice_data or {}).get("status") or order.get("invoice_status") or "NONE"
        status_label = (invoice_data or {}).get("status_label") or INVOICE_LABELS.get(status, status)

        if status in ("SUBMITTED", "PROCESSING", "ISSUED"):
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    f"订单 {order_id} 的开票状态为“{status_label}”。"
                    "开票申请受理与发票实际开具是两个状态，请以订单详情展示为准。"
                ),
                page_action_request=PageActionRequest(
                    action_code="OPEN_ORDER_DETAIL", resource_id=order_id
                ),
            )

        if "APPLY_INVOICE" not in allowed:
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    f"订单 {order_id} 当前状态为"
                    f"{order.get('service_status_label') or SERVICE_LABELS.get(order['service_status'])}，"
                    "目前不能提交开票申请。如订单未支付或已取消，请先确认订单状态。"
                ),
                page_action_request=PageActionRequest(
                    action_code="OPEN_ORDER_DETAIL", resource_id=order_id
                ),
            )

        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=(
                f"订单 {order_id} 可以申请开票，开票金额为 {order.get('total_amount')} 元。"
                "请在开票页面填写发票类型、抬头与接收邮箱后提交；"
                "客服会话不收集完整税号，申请受理后可在订单详情查看进度。"
            ),
            page_action_request=PageActionRequest(
                action_code="APPLY_INVOICE", resource_id=order_id
            ),
        )

    # ----------------------------------------------------------------- 取消
    async def _cancel(self, plan: ExecutionPlan, order: dict[str, Any]) -> AgentOutput:
        order_id = order["id"]
        if "CANCEL_ORDER" in (order.get("allowed_actions") or []):
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    f"订单 {order_id} 尚未开始服务，可以取消。"
                    "请在订单页面核对状态后确认取消；如已支付，退款将按售后流程处理。"
                ),
                page_action_request=PageActionRequest(
                    action_code="CANCEL_ORDER", resource_id=order_id
                ),
            )
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=(
                f"订单 {order_id} 当前服务状态为"
                f"{order.get('service_status_label') or SERVICE_LABELS.get(order['service_status'])}，"
                "服务已经开始，不能直接取消。可以提交售后申请，由人工审核后续处理方式。"
            ),
        )

    # ----------------------------------------------------------------- 查询
    async def _progress(
        self,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
        order: dict[str, Any],
    ) -> AgentOutput:
        order_id = order["id"]
        progress = await self._tool(
            self.tools.get_service_progress(
                context, self._call_id("get_service_progress"), order_id
            )
        )
        if not progress.get("success"):
            return AgentOutput(
                reply_type=ReplyType.ANSWER, reply_content=str(progress.get("message"))
            )
        data = progress["data"]
        missing = data.get("missing_materials") or []
        text = (
            f"订单 {order_id}（{data['service_name']}）当前进度节点是“{data['current_node']}”，"
            f"服务状态为{SERVICE_LABELS.get(data['service_status'], data['service_status'])}。"
        )
        if missing:
            text += f"还需要补充：{'、'.join(missing)}。请在订单详情的服务页面提交材料。"
        if data.get("estimated_at"):
            text += f"业务方提供的预计时间是 {str(data['estimated_at'])[:10]}。"
        else:
            text += "目前没有业务方提供的预计完成时间，以服务记录更新为准。"
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=text,
            page_action_request=PageActionRequest(
                action_code="OPEN_ORDER_DETAIL", resource_id=order_id
            ),
        )

    async def _invoice_query(
        self,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
        order: dict[str, Any],
    ) -> AgentOutput:
        order_id = order["id"]
        invoice = await self._tool(
            self.tools.get_invoice(context, self._call_id("get_invoice"), order_id)
        )
        if not invoice.get("success"):
            return AgentOutput(
                reply_type=ReplyType.ANSWER, reply_content=str(invoice.get("message"))
            )
        data = invoice["data"]
        text = f"订单 {order_id} 的开票状态是“{data['status_label']}”。"
        if data.get("status") == "FAILED" and data.get("failure_reason"):
            text += f"失败原因：{data['failure_reason']}。可在订单详情重新提交申请。"
        text += "受理与开具是两个状态，请以订单详情的最新状态为准。"
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=text,
            page_action_request=PageActionRequest(
                action_code="OPEN_ORDER_DETAIL", resource_id=order_id
            ),
        )

    async def _order_query(self, plan: ExecutionPlan, order: dict[str, Any]) -> AgentOutput:
        order_id = order["id"]
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=(
                f"订单 {order_id} 的服务是{order['service_name']}，"
                f"支付状态为{order.get('payment_status_label') or PAYMENT_LABELS.get(order['payment_status'], order['payment_status'])}，"
                f"服务状态为{order.get('service_status_label') or SERVICE_LABELS.get(order['service_status'], order['service_status'])}，"
                f"订单金额 {order.get('total_amount')} 元。"
            ),
            page_action_request=PageActionRequest(
                action_code="OPEN_ORDER_DETAIL", resource_id=order_id
            ),
        )

    async def _after_sales_query(
        self,
        request: AgentRunRequest,
        context: AgentRuntimeContext,
        plan: ExecutionPlan,
    ) -> AgentOutput:
        order_id = plan.target_resource.get("order_id") or (
            request.flow_state.slots.get("order_id").value
            if request.flow_state and request.flow_state.slots.get("order_id")
            else None
        )
        if order_id:
            # 页面动作需要订单证据，同时再次确认归属
            detail = await self._tool(
                self.tools.get_order(context, self._call_id("get_order"), order_id)
            )
            if not detail.get("success"):
                return AgentOutput(
                    reply_type=ReplyType.ANSWER, reply_content=str(detail.get("message"))
                )
        result = await self._tool(
            self.tools.list_after_sales(
                context, self._call_id("list_after_sales"), order_id
            )
        )
        if not result.get("success"):
            return AgentOutput(
                reply_type=ReplyType.ANSWER, reply_content=str(result.get("message"))
            )
        records = result.get("data") or []
        if not records:
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content="当前没有查到售后申请记录。如果你要发起退款，可以告诉我是哪一笔订单。",
            )
        latest = records[0]
        if not order_id:
            detail = await self._tool(
                self.tools.get_order(
                    context, self._call_id("get_order"), latest["order_id"]
                )
            )
            if not detail.get("success"):
                return AgentOutput(
                    reply_type=ReplyType.ANSWER, reply_content=str(detail.get("message"))
                )
        text = (
            f"售后申请 {latest['id']}（订单 {latest['order_id']}，{latest['kind_label']}）"
            f"当前申请状态为“{latest['application_status_label']}”，"
            f"退款状态为“{latest['refund_status_label']}”。"
            "申请状态与退款到账是不同状态，具体以后续处理记录为准。"
        )
        if len(records) > 1:
            text += f"另有 {len(records) - 1} 条售后记录，可在订单详情查看。"
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=text,
            page_action_request=PageActionRequest(
                action_code="VIEW_AFTER_SALES", resource_id=latest["order_id"]
            ),
        )

    async def _plan_query(
        self,
        request: AgentRunRequest,
        context: AgentRuntimeContext,
        decision: PolicyDecision,
    ) -> AgentOutput:
        query = self._user_text(request)
        result = await self._tool(
            self.tools.search_plans(context, self._call_id("search_plans"), query or "法律服务")
        )
        if not result.get("success"):
            return AgentOutput(
                reply_type=ReplyType.ANSWER, reply_content=str(result.get("message"))
            )
        plans = result.get("data") or []
        if not plans:
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content="没有查到匹配的服务套餐。可以换一个服务名称再试，或转人工客服确认。",
            )
        if len(plans) == 1:
            plan = plans[0]
            detail = await self._tool(
                self.tools.get_plan(context, self._call_id("get_plan"), plan["id"])
            )
            data = detail.get("data") if detail.get("success") else plan
            rights = "、".join(data.get("rights") or [])
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    f"{data['name']} 的价格是 {data['price']} 元，有效期说明："
                    f"{data['validity_description']}。包含权益：{rights}。"
                    f"适用范围：{data.get('service_scope') or '以套餐详情页为准'}。"
                ),
            )
        lines = [
            f"{item['name']}：{item['price']} 元，{item['validity_description']}"
            for item in plans[:5]
        ]
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content="查到以下服务套餐：\n" + "\n".join(f"- {line}" for line in lines),
        )

    async def _policy_query(
        self, request: AgentRunRequest, context: AgentRuntimeContext
    ) -> AgentOutput:
        query = self._user_text(request) or "服务政策"
        result = await self._tool(
            self.tools.search_knowledge(context, self._call_id("search_knowledge"), query)
        )
        if not result.get("success"):
            return AgentOutput(
                reply_type=ReplyType.ANSWER, reply_content=str(result.get("message"))
            )
        items = result.get("data") or []
        if not items:
            return AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content=(
                    "没有检索到与这个问题对应的平台政策，我不能凭经验给出时限或规则。"
                    "可以转人工客服进一步确认。"
                ),
            )
        item = items[0]
        return AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content=(
                f"{item['content']}\n\n依据：{item['title']}（{item['source']}，"
                f"版本 {item['version']}）"
            ),
        )

    @staticmethod
    def _user_text(request: AgentRunRequest) -> str:
        return " ".join(
            str(message.content.get("text", ""))
            for message in request.messages
            if message.type == "text"
        ).strip()


def build_direct_output(
    decision: PolicyDecision, plan: ExecutionPlan
) -> AgentOutput:
    """无需工具时的确定性输出：澄清 / 转人工 / 拒绝。"""
    if plan.planned_action == "HANDOFF" or decision.handoff_reason is not None:
        reason = decision.handoff_reason or HandoffReason.USER_REQUESTED
        summary = decision.handoff_summary or "用户请求人工客服"
        if reason == HandoffReason.LEGAL_ADVICE:
            content = (
                "你提到的问题涉及具体案情判断或法律意见，普通智能客服不能给出这类结论。"
                "我会记录你的诉求，由平台客服协助转交专业法律咨询渠道。"
            )
        elif reason == HandoffReason.COMPLAINT:
            content = "已记录你的投诉诉求，正在为你转接人工客服，请稍候。"
        else:
            content = "正在为你转接人工客服，请稍候。"
        return AgentOutput(
            reply_type=ReplyType.REQUEST_HANDOFF,
            reply_content=content,
            handoff_request=HandoffRequest(reason_code=reason, summary=summary),
        )

    if plan.planned_action == "ASK_CLARIFY":
        # 已知业务槽位使用受控问句，避免模型在无工具阶段举出虚构订单编号。
        question = {
            "order_id": "请问你要办理的是哪一笔订单？可以告诉我订单号。",
            "refund_reason": "方便说明一下申请退款的原因吗？",
        }.get(plan.clarify_slot)
        if not question and decision.clarification and decision.clarification.slot_name == (plan.clarify_slot or "intent"):
            question = decision.clarification.question
        if not question:
            question = {
                "order_id": "请问你要办理的是哪一笔订单？可以告诉我订单号。",
                "refund_reason": "方便说明一下申请退款的原因吗？",
                "intent": "请问你想查询订单进度、了解服务套餐，还是申请退款或开票？",
            }.get(plan.clarify_slot or "intent", "请再说明一下你的具体需求？")
        return AgentOutput(
            reply_type=ReplyType.CLARIFY,
            reply_content=question,
            clarification_slot=plan.clarify_slot or "intent",
        )

    if plan.planned_action == "DECLINE":
        return AgentOutput(
            reply_type=ReplyType.DECLINE,
            reply_content=(
                decision.answer_note
                or "这个问题不在平台客服的处理范围内。可以转人工客服进一步确认。"
            ),
        )

    return AgentOutput(
        reply_type=ReplyType.ANSWER,
        reply_content=decision.answer_note or "请补充一下你的具体问题。",
    )
