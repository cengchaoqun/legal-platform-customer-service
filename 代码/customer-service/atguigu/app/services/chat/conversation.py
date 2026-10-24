from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.respositories.admin.handoff import HandoffRepository
from atguigu.app.respositories.chat.conversation import ConversationRepository
from atguigu.app.respositories.chat.message import MessageRepository
from atguigu.app.respositories.chat.turn import ConversationTurnRepository
from atguigu.app.services.chat.flow import FlowStateService
from atguigu.common.utils import get_utcnow
from atguigu.models.models import Conversation, Message
from atguigu.common.config import get_settings


class ConversationService:
    """
    结论：repo 只负责查询，service 统一提交，确保事务完整性。
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.conversation_repo = ConversationRepository(session)
        self.turn_repo = ConversationTurnRepository(session)
        self.message_repo = MessageRepository(session)
        self.handoff_repo = HandoffRepository(session)
        self.flow_service = FlowStateService(session)

    async def get_current_conversation(self, user_id: str) -> dict[str, Any]:
        conversation = await self.ensure_activate_conversation(user_id)
        is_processing = await self.turn_repo.is_activate_turn(conversation.id)
        handoff = await self.handoff_repo.find_open_by_conversation_id(conversation.id)

        await self.session.commit()
        return {
            "id": conversation.id,
            "mode": conversation.mode,
            "is_processing": is_processing,
            "handoff_status": handoff.status if handoff else None,
        }

    async def ensure_locked_active_conversation(self, user_id: str) -> Conversation:
        """取得并锁定当前有效会话，串行化同一用户的并发发送。"""
        conversation = await self.ensure_activate_conversation(user_id)
        await self.session.refresh(conversation, with_for_update=True)
        return conversation

    async def ensure_activate_conversation(self, user_id: str) -> Conversation:
        """关闭超时会话并返回（或创建）当前有效会话。"""
        await self._close_timeout_conversation(user_id)

        conversation = await self.conversation_repo.find_activate_conversation(
            user_id, ("AI", "QUEUED", "HUMAN")
        )
        if conversation:
            return conversation

        return await self.conversation_repo.add_conversation(user_id)

    async def _close_timeout_conversation(self, user_id: str):
        conversation = await self.conversation_repo.get_ai_conversation(user_id)
        if conversation is not None and _has_idle_timeout(conversation):
            conversation.mode = "CLOSED"
            conversation.ended_at = conversation.last_active_at
            await self.session.flush()

    async def get_conversation_detail(
        self, conversation_id: str, *, requester_id: str, requester_role: str
    ) -> dict[str, Any]:
        """返回授权范围内的会话详情。

        权限：
        - admin 可查看全部会话审计；
        - agent 只能查看存在工单且处于等待 / 自己负责的会话；
        - customer 不能通过该接口读取管理端投影。
        """
        conversation = await self.conversation_repo.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在")

        handoff = await self.handoff_repo.find_latest_by_conversation_id(conversation_id)
        if requester_role == "agent":
            if handoff is None:
                raise HTTPException(status_code=403, detail="没有该会话的授权工单")
            if handoff.status == "waiting":
                pass
            elif handoff.assigned_agent_id != requester_id:
                raise HTTPException(status_code=403, detail="该工单由其他客服负责")
        elif requester_role != "admin":
            raise HTTPException(status_code=403, detail="没有查看会话详情的权限")

        messages = await self.message_repo.get_conversation_messages(conversation_id)
        flow_summary = await self.flow_service.get_summary(conversation_id)

        return {
            "conversation_id": conversation.id,
            "mode": conversation.mode,
            "messages": [get_message_payload(message) for message in messages],
            "handoff_context": (handoff.context_snapshot if handoff else None),
            "flow_summary": flow_summary.model_dump(mode="json") if flow_summary else None,
        }


def get_message_payload(message: Message) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "role": message.role,
        "type": message.message_type,
        "content": message.content,
        "created_at": message.created_at,
    }


def _has_idle_timeout(conversation: Conversation) -> bool:
    now = get_utcnow()
    active_at = conversation.last_active_at
    timeout = timedelta(minutes=get_settings().conversation_idle_timeout_minutes)
    return (now - active_at) >= timeout
