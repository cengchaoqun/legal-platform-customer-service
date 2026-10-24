import pytest

from atguigu.agent.harness.flows.contract import FlowSnapshot, SlotValue
from atguigu.agent.harness.flows.planner import ExecutionPlanner
from atguigu.agent.llm.policy import (
    ClarificationRequest,
    HandoffReasonCode,
    NextAction,
    PolicyDecision,
    PolicyIntent,
    SlotUpdate,
)


def decision(**overrides) -> PolicyDecision:
    payload = {
        "intents": [PolicyIntent.REFUND_APPLY],
        "primary_intent": PolicyIntent.REFUND_APPLY,
        "slot_updates": {},
        "proposed_next_action": NextAction.QUERY_BUSINESS,
    }
    payload.update(overrides)
    return PolicyDecision(**payload)


def snapshot(**overrides) -> FlowSnapshot:
    payload = {"flow_id": "flow_1", "node": "INIT", "state_version": 0}
    payload.update(overrides)
    return FlowSnapshot(**payload)


def test_refund_without_order_queries_list_first():
    plan = ExecutionPlanner().build(decision(), snapshot())
    assert plan.flow_type == "REFUND_APPLY"
    assert plan.node == "IDENTIFY_ORDER"
    assert plan.planned_action == "QUERY_BUSINESS"
    assert "list_orders" in plan.allowed_tool_names
    assert "get_order" in plan.allowed_tool_names


def test_refund_with_reason_offers_application_page():
    plan = ExecutionPlanner().build(
        decision(
            slot_updates={
                "order_id": SlotUpdate(value="ORDER_001", source_message_id="m1"),
                "refund_reason": SlotUpdate(value="买错套餐", source_message_id="m1"),
            },
            proposed_next_action=NextAction.OFFER_PAGE_ACTION,
        ),
        snapshot(flow_type="REFUND_APPLY", node="COLLECT_REASON", state_version=3),
    )
    assert plan.node == "OFFER_APPLICATION"
    assert plan.planned_action == "OFFER_PAGE_ACTION"
    assert "APPLY_AFTER_SALE" in plan.allowed_action_codes
    assert plan.target_resource["order_id"] == "ORDER_001"


def test_offer_without_order_downgrades_to_clarify():
    plan = ExecutionPlanner().build(
        decision(
            intents=[PolicyIntent.CANCEL_ORDER],
            primary_intent=PolicyIntent.CANCEL_ORDER,
            proposed_next_action=NextAction.OFFER_PAGE_ACTION,
        ),
        snapshot(),
    )
    assert plan.planned_action == "ASK_CLARIFY"
    assert plan.clarify_slot == "order_id"
    assert plan.mode == "DIRECT"


def test_policy_query_uses_knowledge_tool_only():
    plan = ExecutionPlanner().build(
        decision(
            intents=[PolicyIntent.POLICY_QUERY],
            primary_intent=PolicyIntent.POLICY_QUERY,
            proposed_next_action=NextAction.QUERY_POLICY,
        ),
        snapshot(),
    )
    assert plan.flow_type == "POLICY_QUERY"
    assert plan.allowed_tool_names == ["search_knowledge"]
    assert plan.mode == "EXECUTE"


def test_human_transfer_is_direct_handoff_without_tools():
    plan = ExecutionPlanner().build(
        decision(
            intents=[PolicyIntent.HUMAN_TRANSFER],
            primary_intent=PolicyIntent.HUMAN_TRANSFER,
            proposed_next_action=NextAction.HANDOFF,
            handoff_reason=HandoffReasonCode.USER_REQUESTED,
            handoff_summary="用户要求人工",
        ),
        snapshot(flow_type="REFUND_APPLY", node="CHECK_ORDER", state_version=2),
    )
    assert plan.mode == "DIRECT"
    assert plan.planned_action == "HANDOFF"
    assert plan.allowed_tool_names == []
    assert plan.flow_type == "HUMAN_TRANSFER"


def test_unparsed_intent_asks_one_intent_question():
    plan = ExecutionPlanner().build(
        decision(
            intents=[PolicyIntent.UNKNOWN],
            primary_intent=PolicyIntent.UNKNOWN,
            proposed_next_action=NextAction.ASK_CLARIFY,
            clarification=ClarificationRequest(slot_name="intent", question="你想查什么？"),
        ),
        snapshot(),
    )
    assert plan.mode == "DIRECT"
    assert plan.clarify_slot == "intent"
    assert plan.allowed_next_actions == ["ANSWER", "ASK_CLARIFY", "HANDOFF", "DECLINE"]


def test_handoff_after_human_resolution_restarts_from_identify():
    plan = ExecutionPlanner().build(
        decision(),
        snapshot(
            flow_type="REFUND_APPLY",
            node="NEEDS_REVALIDATION",
            state_version=5,
            slots={"order_id": SlotValue(value="ORDER_001", validation_status="VERIFIED")},
        ),
    )
    # 人工结束后不能沿用旧资格，必须重新走识别与核对
    assert plan.node == "IDENTIFY_ORDER"
    assert plan.required_slots == ["order_id"]


def test_skill_tool_scope_is_intersected():
    plan = ExecutionPlanner().build(
        decision(
            intents=[PolicyIntent.INVOICE_APPLY],
            primary_intent=PolicyIntent.INVOICE_APPLY,
        ),
        snapshot(),
    )
    # 开票申请属于 order-service，不应出现售后或进度工具
    assert "get_invoice" in plan.allowed_tool_names
    assert "list_after_sales" not in plan.allowed_tool_names
    assert plan.selected_skill == "order-service"
