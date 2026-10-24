"""把已校验的执行结果归约为候选 FlowPatch。

补丁只包含白名单字段，由代码生成；模型不能提交任意状态快照。
"""

from dataclasses import dataclass, field
from typing import Any

from atguigu.agent.harness.flows.catalog import FLOW_CATALOG
from atguigu.agent.harness.flows.contract import (
    FLOW_DEFINITION_VERSION,
    INTENTS,
    FlowPatch,
    FlowPatchChanges,
    FlowSnapshot,
    SlotSourceType,
    SlotValidationStatus,
    SlotValue,
)
from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot
from atguigu.agent.llm.output import ReplyType
from atguigu.agent.llm.policy import ExecutionPlan, NextAction, PolicyDecision
from atguigu.agent.harness.validator.output import ValidatedAgentOutput


@dataclass
class Evidence:
    """本次 Run 中可用于验证槽位的工具证据。"""

    successful_tools: list[str] = field(default_factory=list)
    tool_call_ids: dict[str, str] = field(default_factory=dict)
    order_id: str | None = None
    order_data: dict[str, Any] | None = None
    invoice_data: dict[str, Any] | None = None
    after_sales: list[dict[str, Any]] = field(default_factory=list)
    progress_data: dict[str, Any] | None = None
    plans: list[dict[str, Any]] = field(default_factory=list)
    knowledge: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_business_evidence(self) -> bool:
        return bool(self.successful_tools)


class EvidenceCollector:
    """从已持久化的工具调用快照中读取证据。"""

    @classmethod
    def collect(cls, snapshots: tuple[ToolCallSnapshot, ...]) -> Evidence:
        evidence = Evidence()
        for snapshot in snapshots:
            if snapshot.success is not True:
                continue
            data = (snapshot.result or {}).get("data")
            evidence.successful_tools.append(snapshot.tool_name)
            if snapshot.tool_name in ("get_order", "list_orders", "get_invoice",
                                      "get_service_progress", "list_after_sales",
                                      "search_plans", "get_plan", "search_knowledge"):
                evidence.tool_call_ids[snapshot.tool_name] = str(
                    snapshot.arguments.get("tool_call_id") or ""
                )

            if snapshot.tool_name == "get_order" and isinstance(data, dict):
                evidence.order_id = str(data.get("id") or "")
                evidence.order_data = data
            elif snapshot.tool_name == "list_orders" and isinstance(data, list):
                # 仅一笔时可作为唯一订单依据；多笔必须由用户选择
                if len(data) == 1 and isinstance(data[0], dict):
                    evidence.order_id = str(data[0].get("id") or "")
                    evidence.order_data = data[0]
            elif snapshot.tool_name == "get_invoice" and isinstance(data, dict):
                evidence.invoice_data = data
            elif snapshot.tool_name == "list_after_sales" and isinstance(data, list):
                evidence.after_sales = [item for item in data if isinstance(item, dict)]
            elif snapshot.tool_name == "get_service_progress" and isinstance(data, dict):
                evidence.progress_data = data
            elif snapshot.tool_name == "search_plans" and isinstance(data, list):
                evidence.plans = [item for item in data if isinstance(item, dict)]
            elif snapshot.tool_name == "search_knowledge" and isinstance(data, list):
                evidence.knowledge = [item for item in data if isinstance(item, dict)]
        return evidence


class FlowReducer:
    """根据计划、决策、执行结果与工具证据生成补丁。"""

    def reduce(
        self,
        snapshot: FlowSnapshot,
        plan: ExecutionPlan,
        decision: PolicyDecision,
        output: ValidatedAgentOutput | None,
        evidence: Evidence,
        *,
        run_id: str,
        turn_id: str,
    ) -> FlowPatch:
        # 意图不明确时不要清空当前在办流程，只记录澄清
        if plan.flow_type == "UNKNOWN" and snapshot.flow_type:
            flow_type = snapshot.flow_type
        else:
            flow_type = plan.flow_type if plan.flow_type in INTENTS else None

        changes = FlowPatchChanges(
            flow_type=flow_type,
            node=(
                snapshot.node
                if plan.flow_type == "UNKNOWN" and snapshot.flow_type
                else self._next_node(snapshot, plan, output, evidence)
            ),
            slots=self._slots(plan, decision, evidence),
            pending_intents=self._pending_intents(snapshot, decision),
            clarification_attempts=self._clarification_attempts(
                snapshot, plan, output
            ),
        )
        return FlowPatch(
            patch_id=f"{run_id}:flow:1",
            base_state_version=snapshot.state_version,
            turn_id=turn_id,
            flow_definition_version=FLOW_DEFINITION_VERSION,
            changes=changes,
        )

    # ------------------------------------------------------------------ 节点
    @staticmethod
    def _next_node(
        snapshot: FlowSnapshot,
        plan: ExecutionPlan,
        output: ValidatedAgentOutput | None,
        evidence: Evidence,
    ) -> str:
        flow = FLOW_CATALOG.get(plan.flow_type)
        if output is not None and output.reply_type == ReplyType.REQUEST_HANDOFF:
            return "HANDED_OFF"
        if plan.planned_action == NextAction.HANDOFF.value:
            return "HANDED_OFF"
        if plan.planned_action == NextAction.ASK_CLARIFY.value or (
            output is not None and output.reply_type == ReplyType.CLARIFY
        ):
            slot = (output.clarification_slot if output else None) or plan.clarify_slot
            if flow and slot:
                if slot == "order_id" and flow.has_node("IDENTIFY_ORDER"):
                    return "IDENTIFY_ORDER"
                if slot == "refund_reason" and flow.has_node("COLLECT_REASON"):
                    return "COLLECT_REASON"
            return plan.node
        if plan.planned_action == NextAction.DECLINE.value:
            return plan.node if flow and flow.has_node(plan.node) else "ANSWERED"
        if output is not None and output.page_action is not None:
            if flow and flow.has_node("WAIT_USER_SUBMISSION"):
                return "WAIT_USER_SUBMISSION"
            if flow and flow.has_node("OFFER_APPLICATION"):
                return "OFFER_APPLICATION"

        if flow is None:
            return "ANSWERED" if plan.flow_type == "UNKNOWN" else plan.node

        node_def = flow.node(plan.node) if flow.has_node(plan.node) else None
        if plan.planned_action in (
            NextAction.QUERY_BUSINESS.value,
            NextAction.QUERY_POLICY.value,
        ):
            if evidence.has_business_evidence and node_def and node_def.after_query:
                return node_def.after_query
            return plan.node

        # 申请类流程在回答后保留当前节点，避免一次普通回答就结束流程
        if node_def is not None and node_def.page_action:
            return plan.node
        if node_def is not None and node_def.after_answer:
            return node_def.after_answer
        return "ANSWERED" if flow.has_node("ANSWERED") else plan.node

    # ------------------------------------------------------------------ 槽位
    @staticmethod
    def _slots(
        plan: ExecutionPlan,
        decision: PolicyDecision,
        evidence: Evidence,
    ) -> dict[str, SlotValue]:
        slots: dict[str, SlotValue] = {}
        for name, update in decision.slot_updates.items():
            slots[name] = SlotValue(
                value=update.value,
                source_type=SlotSourceType.USER_MESSAGE,
                source_message_id=update.source_message_id,
                validation_status=SlotValidationStatus.UNVERIFIED,
            )

        if evidence.order_id:
            existing = slots.get("order_id")
            # 工具确认的订单优先；模型从文本抽取的值只在 ID 一致时升级为已验证
            if existing is None or existing.value in (None, evidence.order_id):
                slots["order_id"] = SlotValue(
                    value=evidence.order_id,
                    source_type=SlotSourceType.TOOL_RESULT,
                    source_tool_call_id=evidence.tool_call_ids.get("get_order")
                    or evidence.tool_call_ids.get("list_orders"),
                    validation_status=SlotValidationStatus.VERIFIED,
                )

        if evidence.after_sales:
            latest = evidence.after_sales[0]
            slots["after_sale_id"] = SlotValue(
                value=str(latest.get("id")),
                source_type=SlotSourceType.TOOL_RESULT,
                source_tool_call_id=evidence.tool_call_ids.get("list_after_sales"),
                validation_status=SlotValidationStatus.VERIFIED,
            )
        return slots

    @staticmethod
    def _pending_intents(
        snapshot: FlowSnapshot, decision: PolicyDecision
    ) -> list[str]:
        primary = decision.primary_intent.value
        reserved = {primary, "UNKNOWN", "HUMAN_TRANSFER"}
        pending: list[str] = []
        for intent in [*snapshot.pending_intents, *[item.value for item in decision.intents]]:
            if intent in reserved or intent in pending:
                continue
            pending.append(intent)
        return pending[:3]

    @staticmethod
    def _clarification_attempts(
        snapshot: FlowSnapshot,
        plan: ExecutionPlan,
        output: ValidatedAgentOutput | None,
    ) -> dict[str, int]:
        attempts = dict(snapshot.clarification_attempts)
        is_clarify = (
            output is not None and output.reply_type == ReplyType.CLARIFY
        ) or plan.planned_action == NextAction.ASK_CLARIFY.value
        slot = (output.clarification_slot if output else None) or plan.clarify_slot
        if is_clarify and slot:
            attempts[slot] = attempts.get(slot, 0) + 1
        return attempts
