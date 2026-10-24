"""人工结单后，已由人工处理的转接消息不能重入 AI Turn。"""
from atguigu.app.services.admin.handoff import HandoffService
from atguigu.app.services.chat.turn import TurnService
from datetime import timedelta
from atguigu.common.utils import get_utcnow
from atguigu.models.models import Conversation, ConversationTurn, Handoff, Message


async def test_resolve_advances_ai_input_cursor(session):
    conversation = Conversation(user_id="resume-user", mode="HUMAN", input_revision=3, answered_revision=1)
    session.add(conversation)
    await session.flush()
    handoff = Handoff(conversation_id=conversation.id, user_id=conversation.user_id,
                      summary="显式转人工", status="active", assigned_agent_id="agent_001")
    session.add(handoff)
    await session.commit()
    await HandoffService(session).resolve_handoff(handoff.id, "agent_001")
    assert conversation.answered_revision == 3
    message = Message(conversation_id=conversation.id, message_id="resume-message", role="user",
                      message_type="text", content={"text": "查询 ORDER_002 进度"})
    session.add(message)
    turn = await TurnService(session).add_message_to_turn(message, conversation)
    assert turn.start_revision == 4


async def test_resolve_supersedes_old_collecting_turn(session):
    conversation = Conversation(user_id="pending-user", mode="HUMAN", input_revision=2, answered_revision=0)
    session.add(conversation)
    await session.flush()
    turn = ConversationTurn(conversation_id=conversation.id, user_id=conversation.user_id,
                            status="COLLECTING", start_revision=1,
                            collect_until=get_utcnow(), max_collect_until=get_utcnow()+timedelta(seconds=1))
    handoff = Handoff(conversation_id=conversation.id, user_id=conversation.user_id,
                      summary="收集期间转人工", status="active", assigned_agent_id="agent_001")
    session.add_all([turn, handoff])
    await session.commit()
    await HandoffService(session).resolve_handoff(handoff.id, "agent_001")
    await session.refresh(turn)
    assert turn.status == "SUPERSEDED"
