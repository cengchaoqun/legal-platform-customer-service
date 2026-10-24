import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.respositories.chat.conversation import ConversationRepository
from atguigu.app.respositories.chat.turn import ConversationTurnRepository
from atguigu.app.schemas.chat.flow import FlowPatch, FlowSnapshot
from atguigu.app.services.chat.flow import (
    FlowPatchInvalid,
    FlowStateService,
    FlowVersionConflict,
)
from atguigu.app.services.chat.turn import ClaimToken, TurnService
from atguigu.common.config import get_settings
from atguigu.common.utils import get_utcnow
from atguigu.worker.ai.result import AIResultService

logger = logging.getLogger(__name__)

FALLBACK_TEXT = (
    "当前智能客服暂时无法完成这次处理。你可以稍后再试，"
    "也可以点击“转人工客服”，由人工同事继续为你核实。"
)


class TurnSettlementService:
    """Turn 结算：校验版本、租约与会话模式，然后原子接纳 AI 结果。

    结算原则：
    1. 只有仍由本 Worker 持有、且未过期的租约才能写入；
    2. 会话已转人工、输入已更新或流程版本已变化时，拒绝旧回复与旧补丁；
    3. 消息、流程状态、工单、Turn 终态、Outbox 与 answered_revision 同一事务提交。
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.conversation_repo = ConversationRepository(session)
        self.turn_repo = ConversationTurnRepository(session)
        self.turn_service = TurnService(session)
        self.flow_service = FlowStateService(session)
        self.result_service = AIResultService(session)

    async def finalize_turn(
        self,
        claim: ClaimToken,
        *,
        run_id: str | None,
        outcome: dict[str, Any] | None,
        error: Exception | None,
    ) -> str:
        # 1. 固定锁序：Conversation -> Turn -> FlowState
        conversation = await self.conversation_repo.get_and_lock_by_id(claim.conversation_id)
        if conversation is None:
            await self.session.rollback()
            return "LEASE_LOST"

        turn = await self.turn_repo.get_and_lock_by_id(claim.turn_id)
        if turn is None:
            await self.session.rollback()
            return "LEASE_LOST"

        # 2. 租约与领取尝试校验：被接管或过期后不得写入任何内容
        if (
            turn.status != "RUNNING"
            or turn.locked_by != claim.worker_id
            or turn.attempts != claim.claim_attempt
        ):
            logger.warning(
                "拒绝过期结算 turn=%s worker=%s claim=%s status=%s",
                turn.id,
                claim.worker_id,
                claim.claim_attempt,
                turn.status,
            )
            await self.session.rollback()
            return "LEASE_LOST"

        if turn.locked_until is not None and turn.locked_until < get_utcnow():
            logger.warning("租约已过期，拒绝结算 turn=%s", turn.id)
            turn.last_error = "LEASE_EXPIRED"
            await self.session.commit()
            return "LEASE_LOST"

        # 3. 会话已转人工：放弃 AI 结果，避免覆盖人工处理
        if conversation.mode != "AI":
            if run_id:
                turn.run_id = run_id
            self.turn_service.mark_superseded(turn, "HUMAN_TAKEOVER")
            await self.session.commit()
            return "SUPERSEDED"

        # 4. 输入已更新：用户补发或改口，旧结果不得回复
        if conversation.input_revision != claim.snapshot_revision:
            if run_id:
                turn.run_id = run_id
            self.turn_service.mark_superseded(turn, "INPUT_SUPERSEDED")
            await self.session.commit()
            return "SUPERSEDED"

        # 5. 失败处理：预算内重试，超限使用固定兜底并可选建单
        if error is not None:
            turn.run_id = run_id
            if self.turn_service.retry_or_fail(turn, error):
                await self.session.commit()
                return "RETRIED"
            await self.result_service.save_fallback_result(
                conversation,
                turn,
                FALLBACK_TEXT,
                create_handoff=self.settings.ai_fallback_handoff,
            )
            conversation.answered_revision = claim.snapshot_revision
            await self.session.commit()
            return "FAILED_FALLBACK"

        if outcome is None or run_id is None:
            turn.last_error = "EMPTY_OUTCOME"
            if self.turn_service.retry_or_fail(turn, RuntimeError("AI 未返回结果")):
                await self.session.commit()
                return "RETRIED"
            await self.result_service.save_fallback_result(
                conversation, turn, FALLBACK_TEXT, create_handoff=self.settings.ai_fallback_handoff
            )
            conversation.answered_revision = claim.snapshot_revision
            await self.session.commit()
            return "FAILED_FALLBACK"

        turn.run_id = run_id

        # 6. 流程状态是结算的一部分：先锁行并按版本应用候选补丁
        flow_state = await self.flow_service.lock_state(conversation.id)
        snapshot = self.flow_service._to_snapshot(flow_state)  # noqa: SLF001 - 同模块内部快照
        try:
            snapshot = await self._apply_flow_update(
                conversation.id,
                flow_state,
                outcome.get("flow_update"),
                claim,
            )
        except FlowVersionConflict as exc:
            if turn.flow_recompute_count < 1:
                self.turn_service.requeue_for_recompute(turn, exc)
                await self.session.commit()
                return "RECOMPUTE"
            self.turn_service.mark_superseded(turn, "FLOW_VERSION_CONFLICT")
            await self.session.commit()
            return "SUPERSEDED"
        except (FlowPatchInvalid, ValidationError) as exc:
            logger.error("流程补丁不合法 turn=%s: %s", turn.id, exc)
            self.turn_service.retry_or_fail(turn, RuntimeError("FLOW_PATCH_INVALID"))
            await self.session.commit()
            return "FLOW_PATCH_REJECTED"

        # 7. 保存消息或工单，并完成 Turn
        if outcome.get("outcome_type") == "handoff":
            await self.result_service.save_handoff_result(
                conversation, turn, outcome, flow_snapshot=snapshot
            )
        else:
            await self.result_service.save_ai_result(
                conversation,
                turn,
                outcome["content"],
                message_id=outcome.get("message_id"),
            )

        self.turn_service.mark_completed(turn)
        conversation.answered_revision = claim.snapshot_revision
        await self.session.commit()
        return "COMPLETED"

    async def _apply_flow_update(
        self,
        conversation_id: str,
        flow_state,
        raw_update: Any,
        claim: ClaimToken,
    ) -> FlowSnapshot:
        """校验并应用候选 FlowPatch；没有补丁时返回当前快照。"""
        if raw_update is None:
            return self.flow_service._to_snapshot(flow_state)  # noqa: SLF001
        patch = FlowPatch.model_validate(raw_update)
        return await self.flow_service.apply_patch_locked(
            conversation_id,
            flow_state,
            patch,
            expected_version=claim.base_state_version,
            turn_id=claim.turn_id,
        )
