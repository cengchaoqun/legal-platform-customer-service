from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.respositories.chat.message import MessageRepository
from atguigu.app.schemas.chat.flow import HandoffContextSnapshot
from atguigu.app.services.admin.handoff import HandoffService
from atguigu.app.services.chat.flow import FlowStateService
from atguigu.app.services.realtime import RealTimeOutBoxService
from atguigu.common.utils import get_uid, get_utcnow
from atguigu.models.models import Conversation, ConversationTurn, Message, Handoff


class AIResultService:
    """把 AI 运行结果转换为消息、工单与流程状态。

    该类只负责在当前事务中构建对象，不负责提交；提交由结算服务统一完成，
    保证消息、工单、流程状态、Turn 终态和 Outbox 原子写入。
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.message_repo = MessageRepository(session)
        self.real_service = RealTimeOutBoxService(session)
        self.handoff_service = HandoffService(session)
        self.flow_service = FlowStateService(session)

    async def save_ai_result(
        self,
        conversation: Conversation,
        turn: ConversationTurn,
        content: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> Message:
        message = Message(
            message_id=message_id or get_uid("msg"),
            conversation_id=conversation.id,
            role="ai",
            message_type="text",
            content=content,
            agent_run_id=turn.run_id,
            agent_outcome_seq=1,
        )
        self.message_repo.add(message)
        conversation.last_active_at = get_utcnow()
        await self.session.flush()
        self.real_service.add_message_created_events(
            conversation, message, notify_user=True, notify_staff=False
        )
        return message

    async def save_handoff_result(
        self,
        conversation: Conversation,
        turn: ConversationTurn,
        event_data: dict[str, Any],
        *,
        flow_snapshot=None,
    ) -> Handoff:
        """原子保存转人工提示、开放工单、流程暂停与状态事件。"""
        context = self._build_context(event_data, flow_snapshot)
        handoff = await self.handoff_service.get_or_create_open_handoff(
            conversation,
            summary=str(event_data.get("summary") or "用户请求人工客服"),
            reason_code=str(event_data.get("reason_code") or "USER_REQUESTED"),
            queue_code=str(event_data.get("queue_code") or "platform_aftersales"),
            context_snapshot=context.model_dump(mode="json"),
        )
        conversation.mode = "QUEUED"

        await self.save_ai_result(
            conversation,
            turn,
            event_data["content"],
            message_id=event_data.get("message_id"),
        )

        # 暂停流程：递增版本，使人工期间晚到的 AI 结果无法通过版本校验
        await self.flow_service.pause_for_handoff(
            conversation.id,
            context={
                "reason": str(event_data.get("reason_code") or "USER_REQUESTED"),
                "handoff_id": handoff.id,
            },
            turn_id=turn.id,
        )
        self.real_service.add_handoff_changed_events(conversation, handoff)
        return handoff

    async def save_fallback_result(
        self,
        conversation: Conversation,
        turn: ConversationTurn,
        text: str,
        *,
        create_handoff: bool,
        reason: str = "AI_PROCESSING_FAILED",
    ) -> None:
        """模型或工具故障时的固定兜底：不调用故障模型，直接给出人工入口。"""
        await self.save_ai_result(
            conversation,
            turn,
            {"text": text, "reply_type": "DECLINE", "fallback": True},
        )
        if not create_handoff:
            return

        context = HandoffContextSnapshot(
            summary="AI 处理失败，需要人工继续跟进",
            failure_reason=reason,
            attempted_steps=["AI 处理失败", "固定兜底提示"],
        )
        handoff = await self.handoff_service.get_or_create_open_handoff(
            conversation,
            summary="AI 处理失败，需要人工继续跟进",
            reason_code="RISK_REVIEW",
            context_snapshot=context.model_dump(mode="json"),
        )
        conversation.mode = "QUEUED"
        await self.flow_service.pause_for_handoff(
            conversation.id,
            context={"reason": reason, "handoff_id": handoff.id},
            turn_id=turn.id,
        )
        self.real_service.add_handoff_changed_events(conversation, handoff)

    @staticmethod
    def _build_context(event_data: dict[str, Any], flow_snapshot) -> HandoffContextSnapshot:
        raw = event_data.get("handoff_context")
        if isinstance(raw, dict):
            try:
                return HandoffContextSnapshot.model_validate(raw)
            except ValueError:
                # 结构不合法时保留必要字段，不因交接上下文格式问题阻塞人工入口
                return HandoffContextSnapshot(
                    summary=str(event_data.get("summary") or "用户请求人工客服"),
                    failure_reason="HANDOFF_CONTEXT_INVALID",
                )

        verified: dict[str, str] = {}
        unverified: dict[str, str] = {}
        order_id = None
        if flow_snapshot is not None:
            for name, slot in flow_snapshot.slots.items():
                value = getattr(slot, "value", None)
                if value is None:
                    continue
                if getattr(slot, "validation_status", None) == "VERIFIED":
                    verified[name] = str(value)
                else:
                    unverified[name] = str(value)
            if "order_id" in verified:
                order_id = verified["order_id"]
        return HandoffContextSnapshot(
            intent=flow_snapshot.flow_type if flow_snapshot else None,
            primary_intent=flow_snapshot.flow_type if flow_snapshot else None,
            summary=str(event_data.get("summary") or "用户请求人工客服"),
            order_id=order_id,
            verified_slots=verified,
            unverified_slots=unverified,
            flow_node=flow_snapshot.node if flow_snapshot else None,
            flow_state_version=flow_snapshot.state_version if flow_snapshot else None,
        )
