import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.respositories.chat.message import MessageRepository
from atguigu.app.schemas.chat.flow import HandoffContextSnapshot
from atguigu.app.schemas.chat.message import ChatMessageRequest, MessageType
from atguigu.app.services.admin.handoff import HandoffService
from atguigu.app.services.chat.conversation import ConversationService
from atguigu.app.services.chat.flow import FlowStateService
from atguigu.app.services.chat.turn import TurnService
from atguigu.app.services.realtime import RealTimeOutBoxService, build_message_created_data
from atguigu.common.utils import get_uid, get_utcnow
from atguigu.models.models import Message, Conversation

# 显式转人工前置规则：不依赖模型是否可用
_EXPLICIT_HANDOFF = re.compile(r"转人工|人工客服|找人工|真人客服|人工服务")
_NEGATED_HANDOFF = re.compile(r"不用转人工|不要转人工|不需要人工|先不用人工")
_MAX_HANDOFF_TEXT = 60


def is_explicit_handoff_request(message_type: str, content: dict[str, Any]) -> bool:
    """识别用户显式请求人工客服的短句，避免被模型故障阻塞。"""
    if message_type != MessageType.TEXT:
        return False
    text = str(content.get("text", "")).strip()
    if not text or len(text) > _MAX_HANDOFF_TEXT:
        return False
    if _NEGATED_HANDOFF.search(text):
        return False
    return bool(_EXPLICIT_HANDOFF.search(text))


class MessageService:
    def __init__(self,
                 session: AsyncSession,
                 conversation_service: ConversationService,
                 outbox_service: RealTimeOutBoxService,
                 turn_service: TurnService,
                 handoff_service: HandoffService | None = None):
        self.session = session
        self.conversation_service = conversation_service
        self.outbox_service = outbox_service
        self.turn_service = turn_service
        self.handoff_service = handoff_service or HandoffService(session)
        self.flow_service = FlowStateService(session)
        self.message_repo = MessageRepository(session)

    async def accept_user_message(self, chat_message: ChatMessageRequest, user_id: str) -> dict[str, Any]:
        """受理用户消息。

        事务边界：消息、input_revision、Turn/工单、Outbox 在同一事务提交。
        幂等：同一用户重复提交相同 message_id 且内容相同，返回原受理结果；
        不同用户或不同内容复用同一 ID 属于冲突，不能把消息误归给请求者。
        """
        duplicate = await self.message_repo.find_with_conversation_by_message_id(
            chat_message.message_id
        )
        if duplicate:
            message, conversation = duplicate
            if conversation.user_id != user_id or not self._same_message(message, chat_message):
                raise HTTPException(
                    status_code=409,
                    detail="message_id 已被其他用户或不同内容的消息使用",
                )
            return {
                "conversation_id": conversation.id,
                "mode": conversation.mode,
                "handoff_created": conversation.mode in ("QUEUED", "HUMAN"),
            }

        conversation = await self.conversation_service.ensure_locked_active_conversation(user_id)

        if is_explicit_handoff_request(chat_message.type, chat_message.content):
            return await self._accept_explicit_handoff(chat_message, conversation)

        message = self.save_message(
            conversation,
            chat_message.content,
            chat_message.message_id,
            message_role="user",
            message_type=chat_message.type,
        )

        if conversation.mode == "AI":
            await self.turn_service.add_message_to_turn(message, conversation)
        elif conversation.mode in ("QUEUED", "HUMAN"):
            # 人工模式下只保存与通知，不触发 AI，也不创建 Turn
            await self.session.flush()
            self.outbox_service.add_message_created_events(
                conversation, message, notify_user=False, notify_staff=True
            )
        else:
            raise ValueError(f"当前会话模式{conversation.mode}不支持")

        await self.session.commit()
        return {
            "conversation_id": conversation.id,
            "mode": conversation.mode,
            "handoff_created": False,
        }

    async def _accept_explicit_handoff(
        self, chat_message: ChatMessageRequest, conversation: Conversation
    ) -> dict[str, Any]:
        """显式转人工分支：保存消息、建单、暂停流程、通知客服同事务完成。"""
        conversation.input_revision += 1
        message = self.save_message(
            conversation,
            chat_message.content,
            chat_message.message_id,
            message_role="user",
            message_type=chat_message.type,
        )
        message.input_revision = conversation.input_revision

        snapshot = await self.flow_service.load_snapshot(conversation.id)
        context = HandoffContextSnapshot(
            intent=snapshot.flow_type,
            primary_intent=snapshot.flow_type,
            summary=str(chat_message.content.get("text", "用户请求人工客服")),
            order_id=(snapshot.slots.get("order_id").value if snapshot.slots.get("order_id") else None),
            flow_node=snapshot.node,
            flow_state_version=snapshot.state_version,
            source_message_ids=[chat_message.message_id],
            attempted_steps=["用户主动请求人工"],
        )

        handoff = await self.handoff_service.get_or_create_open_handoff(
            conversation,
            summary=f"用户主动请求人工客服：{context.summary}",
            reason_code="USER_REQUESTED",
            context_snapshot=context.model_dump(mode="json"),
        )
        # 已有人工客服负责时保持 HUMAN（重复“转人工”不打断进行中的服务）；
        # 新建或排队中的工单进入 QUEUED 等待接入。
        conversation.mode = "HUMAN" if handoff.status == "active" else "QUEUED"
        if conversation.mode == "QUEUED":
            await self.flow_service.pause_for_handoff(
                conversation.id,
                context={"reason": "USER_REQUESTED", "handoff_id": handoff.id},
            )

        await self.session.flush()
        self.outbox_service.add_message_created_events(
            conversation, message, notify_user=False, notify_staff=True
        )
        self.outbox_service.add_handoff_changed_events(conversation, handoff)
        await self.session.commit()
        return {
            "conversation_id": conversation.id,
            "mode": conversation.mode,
            "handoff_created": True,
        }

    def save_message(self,
                     conversation: Conversation,
                     message_content: dict[str, Any],
                     message_id: str | None,
                     *,
                     message_role: str,
                     message_type: str = "text"
                     ) -> Message:
        """在当前事务中创建消息。"""
        message = Message(
            message_id=message_id or get_uid("msg"),
            conversation_id=conversation.id,
            role=message_role,
            message_type=message_type,
            content=message_content,
        )
        conversation.last_active_at = get_utcnow()
        self.message_repo.add_message(message)
        return message

    async def get_history(self, user_id: str, after_sequence: int | None = None) -> list[dict[str, Any]]:
        """返回用户历史消息；重连时可按 sequence 增量查询。"""
        return [
            {
                **build_message_created_data(message),
                "conversation_started_at": conversation.started_at.isoformat(),
            }
            for message, conversation in (
                await self.message_repo.list_user_history(user_id, after_sequence)
            )
        ]

    @staticmethod
    def _same_message(message: Message, request: ChatMessageRequest) -> bool:
        return (
            message.message_type == request.type
            and dict(message.content or {}) == dict(request.content or {})
        )
