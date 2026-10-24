from datetime import timedelta

import pytest
from sqlalchemy import func, select

from atguigu.app.schemas.chat.flow import FlowPatch
from atguigu.app.services.chat.flow import FlowStateService
from atguigu.app.services.chat.turn import TurnService
from atguigu.common.utils import get_utcnow
from atguigu.infrastucture.db import session_factory
from atguigu.models.models import (
    Conversation,
    ConversationFlowState,
    ConversationTurn,
    Message,
    RealtimeOutbox,
)
from atguigu.worker.ai.settlement import TurnSettlementService


async def prepare_turn(user_id="user_001", text="合同审核套餐我想退款"):
    async with session_factory() as session:
        conversation = Conversation(user_id=user_id, mode="AI", input_revision=1)
        session.add(conversation)
        await session.flush()
        message = Message(
            message_id=f"msg_{conversation.id}",
            conversation_id=conversation.id,
            role="user",
            message_type="text",
            content={"text": text},
            input_revision=1,
        )
        turn = ConversationTurn(
            conversation_id=conversation.id,
            user_id=user_id,
            status="COLLECTING",
            start_revision=1,
            collect_until=get_utcnow() - timedelta(seconds=1),
            max_collect_until=get_utcnow() + timedelta(seconds=5),
        )
        session.add_all([message, turn])
        await session.commit()
        return conversation.id, turn.id


async def claim(turn_id):
    async with session_factory() as session:
        service = TurnService(session)
        claimed = await service.claim_turn("worker-test")
        assert claimed is not None
        turn, token = claimed
        assert turn.id == turn_id
        await session.commit()
        return token


def outcome(token, *, base_version=0, order_id="ORDER_001", text="请确认退款原因。"):
    return {
        "outcome_type": "message",
        "message_id": "msg_ai_result",
        "content": {"text": text, "reply_type": "ANSWER"},
        "flow_update": {
            "patch_id": f"{token.turn_id}:flow:1",
            "base_state_version": base_version,
            "turn_id": token.turn_id,
            "flow_definition_version": "legal-support-flow-v1",
            "changes": {
                "flow_type": "REFUND_APPLY",
                "node": "COLLECT_REASON",
                "slots": {
                    "order_id": {
                        "value": order_id,
                        "source_type": "TOOL_RESULT",
                        "validation_status": "VERIFIED",
                    }
                },
                "pending_intents": [],
                "clarification_attempts": {},
            },
        },
    }


async def finalize(token, *, run_id="run_test", result=None, error=None):
    async with session_factory() as session:
        service = TurnSettlementService(session)
        return await service.finalize_turn(
            token, run_id=run_id, outcome=result, error=error
        )


@pytest.mark.asyncio
async def test_successful_settlement_is_atomic():
    conversation_id, turn_id = await prepare_turn()
    token = await claim(turn_id)

    disposition = await finalize(token, result=outcome(token))
    assert disposition == "COMPLETED"

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, turn_id)
        conversation = await session.get(Conversation, conversation_id)
        flow = await session.get(ConversationFlowState, conversation_id)
        ai_messages = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id, Message.role == "ai")
        )
        events = await session.scalar(select(func.count()).select_from(RealtimeOutbox))

    assert turn.status == "COMPLETED"
    assert conversation.answered_revision == 1
    assert flow.state_version == 1
    assert flow.node == "COLLECT_REASON"
    assert ai_messages == 1
    assert events >= 1


@pytest.mark.asyncio
async def test_human_takeover_rejects_in_flight_ai_result():
    conversation_id, turn_id = await prepare_turn()
    token = await claim(turn_id)

    async with session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.mode = "HUMAN"
        await session.commit()

    disposition = await finalize(token, result=outcome(token))
    assert disposition == "SUPERSEDED"

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, turn_id)
        ai_messages = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id, Message.role == "ai")
        )
        flow = await session.get(ConversationFlowState, conversation_id)
    assert turn.status == "SUPERSEDED"
    assert turn.last_error == "HUMAN_TAKEOVER"
    assert ai_messages == 0
    assert flow is None


@pytest.mark.asyncio
async def test_new_user_input_supersedes_old_turn():
    conversation_id, turn_id = await prepare_turn()
    token = await claim(turn_id)

    async with session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.input_revision = 2
        await session.commit()

    disposition = await finalize(token, result=outcome(token))
    assert disposition == "SUPERSEDED"

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, turn_id)
    assert turn.last_error == "INPUT_SUPERSEDED"


@pytest.mark.asyncio
async def test_lease_lost_when_turn_was_reclaimed():
    conversation_id, turn_id = await prepare_turn()
    token = await claim(turn_id)

    # 模拟租约超时后恢复：Turn 回到 COLLECTING，旧 Worker 再返回
    async with session_factory() as session:
        turn = await session.get(ConversationTurn, turn_id)
        turn.status = "COLLECTING"
        turn.locked_by = None
        turn.locked_until = None
        await session.commit()

    disposition = await finalize(token, result=outcome(token))
    assert disposition == "LEASE_LOST"

    async with session_factory() as session:
        ai_messages = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id, Message.role == "ai")
        )
    assert ai_messages == 0


@pytest.mark.asyncio
async def test_flow_version_conflict_triggers_one_recompute():
    conversation_id, turn_id = await prepare_turn()
    token = await claim(turn_id)

    # 另一个流程更新先落地，版本从 0 变为 1
    async with session_factory() as session:
        service = FlowStateService(session)
        await service.apply_patch_locked(
            conversation_id,
            None,
            FlowPatch(
                patch_id="other:flow:1",
                base_state_version=0,
                turn_id="another_turn",
                flow_definition_version="legal-support-flow-v1",
                changes={"flow_type": "REFUND_APPLY", "node": "IDENTIFY_ORDER"},
            ),
            expected_version=0,
            turn_id="another_turn",
        )
        await session.commit()

    disposition = await finalize(token, result=outcome(token, base_version=0))
    assert disposition == "RECOMPUTE"

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, turn_id)
    assert turn.status == "COLLECTING"
    assert turn.flow_recompute_count == 1


@pytest.mark.asyncio
async def test_failure_retries_within_budget_before_fallback():
    conversation_id, turn_id = await prepare_turn()
    token = await claim(turn_id)

    disposition = await finalize(token, error=RuntimeError("AI Service 网络调用失败"))
    assert disposition == "RETRIED"

    async with session_factory() as session:
        turn = await session.get(ConversationTurn, turn_id)
        ai_messages = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id, Message.role == "ai")
        )
    assert turn.status == "COLLECTING"
    assert turn.last_error
    assert ai_messages == 0
