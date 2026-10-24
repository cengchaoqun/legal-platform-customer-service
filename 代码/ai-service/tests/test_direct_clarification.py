"""真实模型澄清里可能带示例订单号，代码模板不得把示例当业务事实。"""
from atguigu.agent.harness.run.fixture_agent import build_direct_output
from atguigu.agent.llm.policy import PolicyDecision, ExecutionPlan


def test_order_clarification_does_not_repeat_unverified_example():
    decision = PolicyDecision.model_validate({
        "intents": ["REFUND_APPLY"], "primary_intent": "REFUND_APPLY",
        "proposed_next_action": "ASK_CLARIFY",
        "clarification": {"slot_name": "order_id", "question": "请提供订单号，例如 ORDER_001？"},
    })
    plan = ExecutionPlan.model_validate({
        "flow_type": "REFUND_APPLY", "node": "IDENTIFY_ORDER", "selected_skill": "after_sale",
        "allowed_tool_names": [], "required_slots": ["order_id"], "missing_slots": ["order_id"],
        "allowed_next_actions": ["ASK_CLARIFY"], "mode": "DIRECT",
        "planned_action": "ASK_CLARIFY", "clarify_slot": "order_id",
    })
    output = build_direct_output(decision, plan)
    assert "ORDER_001" not in output.reply_content
    assert output.clarification_slot == "order_id"
