# -*- coding: utf-8 -*-
"""回归测试：fixture 决策器的意图检测只针对最新一条用户消息。

背景：人工接管结束后，历史里出现过“转人工”，此前把全部历史拼接后
做关键词检测，导致用户后续问“退款申请状态”被误判为转人工。
"""

import pytest

from atguigu.agent.harness.run.fixture_policy import FixturePolicyPredictor
from atguigu.app.schemas.run import AgentRunRequest


def _request(texts, flow_state=None):
    messages = [
        {"message_id": f"m{i}", "type": "text", "content": {"text": t}}
        for i, t in enumerate(texts, start=1)
    ]
    return AgentRunRequest(
        contract_version="legal-support-v2",
        conversation_id="conv_test",
        user_id="user_001",
        turn_id="turn_test",
        request_id="req_test",
        input_revision=1,
        flow_state_version=(flow_state.state_version if flow_state else 0),
        flow_state=flow_state,
        messages=messages,
    )


@pytest.mark.asyncio
async def test_intent_uses_latest_message_not_history():
    """历史消息里出现“转人工”，最新消息问退款状态时不得判为转人工。"""
    predictor = FixturePolicyPredictor()
    request = _request(
        [
            "我涉及具体案件纠纷，需要转人工客服处理",
            "有人吗，我等下提供材料",
            "好的，那我的退款申请现在是什么状态？",
        ]
    )
    decision = await predictor.predict(request)
    assert decision.primary_intent is not None
    assert decision.handoff_reason is None, (
        "历史中的“转人工”不应污染后续轮次的意图判定"
    )


@pytest.mark.asyncio
async def test_latest_message_handoff_still_detected():
    """最新消息确实是转人工时仍要正确识别。"""
    predictor = FixturePolicyPredictor()
    request = _request(["退款进度怎么样了", "算了，还是转人工吧"])
    decision = await predictor.predict(request)
    assert decision.handoff_reason is not None


@pytest.mark.asyncio
async def test_object_message_keeps_flow_intent():
    """用户点选澄清候选（纯对象消息）时沿用当前流程意图。"""
    from atguigu.agent.harness.flows.contract import FlowSnapshot

    snapshot = FlowSnapshot(
        flow_id="flow_test",
        flow_type="REFUND_APPLY",
        node="IDENTIFY_ORDER",
        slots={},
        state_version=2,
    )
    predictor = FixturePolicyPredictor()
    request = AgentRunRequest(
        contract_version="legal-support-v2",
        conversation_id="conv_test",
        user_id="user_001",
        turn_id="turn_test",
        request_id="req_test",
        input_revision=1,
        flow_state_version=2,
        flow_state=snapshot,
        messages=[
            {
                "message_id": "m1",
                "type": "object",
                "content": {"object_type": "order", "object_id": "ORDER_001"},
            }
        ],
    )
    decision = await predictor.predict(request)
    assert decision.primary_intent.value == "REFUND_APPLY"
