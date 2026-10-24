from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.respositories.admin.handoff import HandoffRepository
from atguigu.app.respositories.chat.message import MessageRepository
from atguigu.app.services.chat.flow import FlowStateService
from atguigu.app.services.realtime import RealTimeOutBoxService
from atguigu.common.utils import get_utcnow, get_uid
from atguigu.models.models import Conversation, ConversationTurn, Handoff, Message

# 首版只有平台售后队列真实存在人工承接方；专业法律咨询必须先确认承接渠道
QUEUE_PLATFORM_AFTERSALES = "platform_aftersales"
QUEUE_PROFESSIONAL_REVIEW = "professional_review"


class HandoffService:
    """管理人工工单的创建、接单、回复和结束。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.handoff_repository = HandoffRepository(session)
        self.message_repository = MessageRepository(session)
        self.real_service = RealTimeOutBoxService(session)
        self.flow_service = FlowStateService(session)

    async def get_or_create_open_handoff(
        self,
        conversation: Conversation,
        *,
        summary: str,
        reason_code: str = "USER_REQUESTED",
        queue_code: str = QUEUE_PLATFORM_AFTERSALES,
        context_snapshot: dict[str, Any] | None = None,
    ) -> Handoff:
        """查询开放工单，不存在时创建 waiting 工单。"""
        handoff = await self.handoff_repository.find_open_by_user_id(conversation.user_id)

        if handoff is not None:
            # 复用开放工单时更新摘要与上下文，不重复建单
            handoff.summary = summary
            handoff.reason_code = reason_code or handoff.reason_code
            handoff.queue_code = queue_code or handoff.queue_code
            if context_snapshot:
                handoff.context_snapshot = context_snapshot
            return handoff

        handoff = Handoff(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            summary=summary,
            status="waiting",
            reason_code=reason_code,
            queue_code=queue_code,
            context_snapshot=context_snapshot,
        )
        self.handoff_repository.add(handoff)
        await self.session.flush()
        return handoff

    async def list_open_handoffs(self) -> list[dict[str, Any]]:
        """返回等待中和服务中的工单，并附带最小上下文预览。"""
        handoffs = await self.handoff_repository.list_open()
        return [
            {
                "id": handoff.id,
                "conversation_id": handoff.conversation_id,
                "user_id": handoff.user_id,
                "summary": handoff.summary,
                "status": handoff.status,
                "reason_code": handoff.reason_code,
                "queue_code": handoff.queue_code or QUEUE_PLATFORM_AFTERSALES,
                "assigned_agent_id": handoff.assigned_agent_id,
                "created_at": handoff.created_at,
                "context_preview": self._context_preview(handoff),
            }
            for handoff in handoffs
        ]

    @staticmethod
    def _context_preview(handoff: Handoff) -> dict[str, Any] | None:
        context = handoff.context_snapshot or {}
        if not context:
            return None
        return {
            "intent": context.get("primary_intent") or context.get("intent"),
            "order_id": context.get("order_id"),
            "after_sale_id": context.get("after_sale_id"),
            "verified_slots": context.get("verified_slots") or {},
            "failure_reason": context.get("failure_reason"),
        }

    async def accept_handoff(self, handoff_id: str, agent_id: str) -> None:
        """由客服接单，并将会话切换为 HUMAN。"""
        handoff, conversation = await self._get_open_handoff(handoff_id)

        # active 工单只允许原负责人重复接单；其他客服重试会得到 403
        if handoff.status == "active":
            self._require_owner(handoff, agent_id)
            return

        handoff.status = "active"
        handoff.assigned_agent_id = agent_id
        handoff.accepted_at = get_utcnow()
        conversation.mode = "HUMAN"
        conversation.last_active_at = get_utcnow()

        # 暂停 AI 流程：递增流程版本，使人工期间晚到的 AI 补丁无法结算
        await self.flow_service.pause_for_handoff(
            conversation.id,
            context={
                "reason": "HUMAN_ACCEPTED",
                "handoff_id": handoff.id,
                "agent_id": agent_id,
            },
        )

        self.real_service.add_handoff_changed_events(conversation, handoff)
        await self.session.commit()

    async def reply_handoff(
        self,
        handoff_id: str,
        agent_id: str,
        *,
        message_id: str,
        text: str,
    ) -> None:
        """由当前负责人保存人工回复。"""
        handoff, conversation = await self._get_open_handoff(handoff_id)
        self._require_owner(handoff, agent_id)

        message = self._add_human_message(conversation, text, message_id=message_id)
        await self.session.flush()
        self.real_service.add_message_created_events(
            conversation, message, notify_user=True, notify_staff=True
        )
        await self.session.commit()

    async def resolve_handoff(
        self,
        handoff_id: str,
        agent_id: str,
        *,
        resolution: dict[str, Any] | None = None,
    ) -> None:
        """结束当前客服负责的工单，并将会话切回 AI。

        结束人工不等于自动继续旧申请：流程进入 NEEDS_REVALIDATION，
        下一条用户输入需要重新核实业务事实。
        """
        handoff, conversation = await self._get_open_handoff(handoff_id)
        self._require_owner(handoff, agent_id)

        handoff.status = "resolved"
        handoff.resolved_at = get_utcnow()
        handoff.resolution_context = resolution or {
            "resolution": "RESOLVED_BY_AGENT",
            "agent_id": agent_id,
            "resolved_at": get_utcnow().isoformat(),
        }

        conversation.mode = "AI"
        # 人工已承接结单前的输入。推进 AI 游标，避免旧转接诉求重入下一轮。
        # 该会话锁也保护新消息受理，后续输入会取得更大的 revision。
        conversation.answered_revision = conversation.input_revision
        pending_turns = await self.session.scalars(
            select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation.id,
                ConversationTurn.status.in_(["COLLECTING", "RUNNING"]),
            ).with_for_update()
        )
        for turn in pending_turns:
            turn.status = "SUPERSEDED"
            turn.last_error = "HUMAN_RESOLVED"
            turn.finished_at = get_utcnow()
            turn.locked_by = None
            turn.locked_until = None
        conversation.last_active_at = get_utcnow()

        await self.flow_service.prepare_resume(
            conversation.id, resolution=handoff.resolution_context
        )

        message = self._add_human_message(
            conversation,
            "人工客服已结束服务，后续将由 AI 客服继续为您服务。"
            "如需继续办理，请重新说明当前诉求，我们会先核对最新业务状态。",
        )
        await self.session.flush()
        self.real_service.add_message_created_events(
            conversation, message, notify_user=True, notify_staff=False
        )
        self.real_service.add_handoff_changed_events(conversation, handoff)
        await self.session.commit()

    async def _get_open_handoff(self, handoff_id: str) -> tuple[Handoff, Conversation]:
        """锁定开放工单及其所属会话。"""
        handoff, conversation = (
            await self.handoff_repository.find_and_lock_with_conversation_by_id(handoff_id)
        )
        if handoff.status == "resolved":
            raise HTTPException(status_code=409, detail="工单已经结束")
        return handoff, conversation

    @staticmethod
    def _require_owner(handoff: Handoff, agent_id: str):
        """校验工单属于当前客服。"""
        if handoff.assigned_agent_id != agent_id:
            raise HTTPException(status_code=403, detail="工单不属于当前客服")

    def _add_human_message(
        self,
        conversation: Conversation,
        text: str,
        *,
        message_id: str | None = None,
    ) -> Message:
        """创建人工消息并加入当前事务。"""
        message = Message(
            message_id=message_id or get_uid("msg"),
            conversation_id=conversation.id,
            role="human",
            message_type="text",
            content={"text": text},
        )
        self.message_repository.add(message)
        conversation.last_active_at = get_utcnow()
        return message
