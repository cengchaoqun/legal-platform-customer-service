import pytest

from atguigu.app.schemas.chat.flow import FlowPatch, SlotValue
from atguigu.app.services.chat.flow import (
    FlowStateService,
    FlowVersionConflict,
)
from atguigu.models.models import Conversation


async def create_conversation(session, user_id="user_001"):
    conversation = Conversation(user_id=user_id, mode="AI")
    session.add(conversation)
    await session.commit()
    return conversation


def patch(base: int, *, order_id=None, reason=None, node="COLLECT_REASON", turn="turn_1"):
    slots = {}
    if order_id:
        slots["order_id"] = SlotValue(
            value=order_id, validation_status="VERIFIED", source_type="TOOL_RESULT"
        )
    if reason:
        slots["refund_reason"] = SlotValue(value=reason)
    return FlowPatch(
        patch_id=f"patch_{base}_{turn}",
        base_state_version=base,
        turn_id=turn,
        flow_definition_version="legal-support-flow-v1",
        changes={
            "flow_type": "REFUND_APPLY",
            "node": node,
            "slots": slots,
            "pending_intents": [],
            "clarification_attempts": {},
        },
    )


@pytest.mark.asyncio
async def test_first_patch_creates_state_and_persists_slots(session):
    conversation = await create_conversation(session)
    service = FlowStateService(session)

    snapshot = await service.apply_patch_locked(
        conversation.id,
        None,
        patch(0, order_id="ORDER_001", reason="买错套餐"),
        expected_version=0,
        turn_id="turn_1",
    )
    assert snapshot.state_version == 1
    assert snapshot.node == "COLLECT_REASON"
    assert snapshot.slots["order_id"].value == "ORDER_001"
    assert snapshot.slots["order_id"].validation_status == "VERIFIED"

    reloaded = await service.load_snapshot(conversation.id)
    assert reloaded.state_version == 1
    assert reloaded.slots["refund_reason"].value == "买错套餐"


@pytest.mark.asyncio
async def test_switching_order_clears_dependent_values(session):
    conversation = await create_conversation(session)
    service = FlowStateService(session)
    await service.apply_patch_locked(
        conversation.id,
        None,
        patch(0, order_id="ORDER_001", reason="买错套餐"),
        expected_version=0,
        turn_id="turn_1",
    )
    current = await service.lock_state(conversation.id)

    snapshot = await service.apply_patch_locked(
        conversation.id,
        current,
        patch(1, order_id="ORDER_004", node="CHECK_ORDER", turn="turn_2"),
        expected_version=1,
        turn_id="turn_2",
    )
    assert snapshot.slots["order_id"].value == "ORDER_004"
    # 旧订单的退款原因必须失效，不能被带到新订单
    assert "refund_reason" not in snapshot.slots
    assert snapshot.slots["order_id"].history
    assert snapshot.slots["order_id"].history[-1]["value"] == "ORDER_001"


@pytest.mark.asyncio
async def test_stale_base_version_is_rejected(session):
    conversation = await create_conversation(session)
    service = FlowStateService(session)
    await service.apply_patch_locked(
        conversation.id,
        None,
        patch(0, order_id="ORDER_001"),
        expected_version=0,
        turn_id="turn_1",
    )
    current = await service.lock_state(conversation.id)

    with pytest.raises(FlowVersionConflict):
        await service.apply_patch_locked(
            conversation.id,
            current,
            patch(0, order_id="ORDER_004", turn="turn_2"),
            expected_version=0,
            turn_id="turn_2",
        )


@pytest.mark.asyncio
async def test_pause_and_resume_advance_version_without_reusing_old_state(session):
    conversation = await create_conversation(session)
    service = FlowStateService(session)
    await service.apply_patch_locked(
        conversation.id,
        None,
        patch(0, order_id="ORDER_001", reason="买错套餐"),
        expected_version=0,
        turn_id="turn_1",
    )

    paused = await service.pause_for_handoff(
        conversation.id, context={"reason": "USER_REQUESTED"}, turn_id="turn_1"
    )
    assert paused.node == "HANDED_OFF"
    assert paused.state_version == 2

    resumed = await service.prepare_resume(
        conversation.id, resolution={"resolution": "RESOLVED"}
    )
    assert resumed.node == "NEEDS_REVALIDATION"
    assert resumed.state_version == 3
