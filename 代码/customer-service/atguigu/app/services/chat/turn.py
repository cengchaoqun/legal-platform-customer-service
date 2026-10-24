from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.respositories.chat.conversation import ConversationRepository
from atguigu.app.respositories.chat.flow import FlowStateRepository
from atguigu.app.respositories.chat.message import MessageRepository
from atguigu.app.respositories.chat.turn import ConversationTurnRepository
from atguigu.app.schemas.chat.flow import FLOW_DEFINITION_VERSION, FlowSnapshot
from atguigu.common.utils import get_utcnow
from atguigu.models.models import Conversation, ConversationTurn, Message
from atguigu.common.config import get_settings


@dataclass(frozen=True)
class ClaimToken:
    """一次 Turn 领取的不可变凭据。

    结算时必须逐项比对：Turn 状态、worker、claim 尝试次数、输入版本和流程版本。
    只要有一项不匹配，就说明结果已经过期或已被其他 Worker 接管。
    """

    turn_id: str
    conversation_id: str
    user_id: str
    worker_id: str
    claim_attempt: int
    snapshot_revision: int
    base_state_version: int


class TurnService:
    def __init__(self, session: AsyncSession):
        self.setting = get_settings()
        self.session = session
        self.turn_repo = ConversationTurnRepository(session)
        self.conver_repo = ConversationRepository(session)
        self.message_repo = MessageRepository(session)
        self.flow_repo = FlowStateRepository(session)

    async def add_message_to_turn(self, message: Message, conversation: Conversation) -> ConversationTurn:
        """把用户消息并入当前收集中的 Turn，没有则创建新 Turn。

        - 已存在 COLLECTING：延长收集窗口，合并为用户一次表达；
        - 已存在 RUNNING：说明本轮已被 Worker 领取，创建新的 Turn，保证串行。
        """
        conversation.input_revision += 1
        message.input_revision = conversation.input_revision

        turn = await self.turn_repo.find_conversation_by_id(conversation.id)
        now = get_utcnow()
        delay = timedelta(milliseconds=self.setting.message_merge_delay_ms)
        if turn:
            turn.collect_until = min(now + delay, turn.max_collect_until)
            return turn

        turn = ConversationTurn(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            status="COLLECTING",
            collect_until=now + delay,
            start_revision=conversation.answered_revision + 1,
            max_collect_until=now + timedelta(milliseconds=self.setting.message_merge_max_wait_ms),
        )
        self.turn_repo.add_turn(turn)
        return turn

    async def claim_turn(self, worker_id: str) -> tuple[ConversationTurn, ClaimToken] | None:
        """领取一个到期 Turn，并固化领取快照。

        领取后立即由调用方提交短事务，模型与工具调用期间不持有数据库锁。
        """
        now = get_utcnow()
        claimed = await self.turn_repo.find_ready_turn_conversation_by_lock(now)
        if claimed is None:
            return None

        turn, conversation = claimed
        flow_state = await self.flow_repo.find_by_conversation_id(conversation.id)
        base_state_version = flow_state.state_version if flow_state is not None else 0

        turn.status = "RUNNING"
        turn.snapshot_revision = conversation.input_revision
        turn.locked_by = worker_id
        turn.locked_until = now + timedelta(seconds=self.setting.ai_worker_lease_seconds)
        turn.attempts += 1
        turn.started_at = turn.started_at or now
        turn.base_flow_state_version = base_state_version

        claim = ClaimToken(
            turn_id=turn.id,
            conversation_id=conversation.id,
            user_id=turn.user_id,
            worker_id=worker_id,
            claim_attempt=turn.attempts,
            snapshot_revision=conversation.input_revision,
            base_state_version=base_state_version,
        )
        return turn, claim

    async def build_ai_request_data(
        self, claimed_turn: ConversationTurn, claim: ClaimToken
    ) -> tuple[dict[str, Any], str]:
        """构建 AI Service V2 请求。

        请求显式携带合同版本、输入版本与流程快照；流程状态不再依赖聊天历史。
        """
        snapshot_revision = claimed_turn.snapshot_revision
        current_messages = await self.message_repo.find_current_messages_by_turn_range(
            claimed_turn.conversation_id, claimed_turn.start_revision, snapshot_revision
        )
        if not current_messages:
            raise RuntimeError("当前 Turn 没有可处理的消息")

        history_messages = list(
            reversed(
                await self.message_repo.find_history_message_by_sequence(
                    claimed_turn.conversation_id, current_messages[0].id
                )
            )
        )
        flow_state = await self.flow_repo.find_by_conversation_id(claimed_turn.conversation_id)
        flow_snapshot = self._flow_snapshot(flow_state)

        request_data = {
            "contract_version": "legal-support-v2",
            "request_id": f"{claimed_turn.id}:attempt:{claimed_turn.attempts}",
            "conversation_id": claimed_turn.conversation_id,
            "turn_id": claimed_turn.id,
            "user_id": claimed_turn.user_id,
            "input_revision": snapshot_revision,
            "flow_state_version": claim.base_state_version,
            "flow_state": flow_snapshot.model_dump(mode="json") if flow_state else None,
            "messages": [
                {
                    "message_id": message.message_id,
                    "type": message.message_type,
                    "content": message.content,
                }
                for message in current_messages
            ],
            "history": [
                {
                    "message_id": message.message_id,
                    "role": message.role,
                    "type": message.message_type,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
                for message in history_messages
            ],
        }
        return request_data, current_messages[-1].message_id

    async def renew_lease(self, claim: ClaimToken) -> bool:
        """为仍持有租约的 Turn 续期；被接管时返回 False。"""
        turn = await self.turn_repo.find_by_id(claim.turn_id)
        if turn is None:
            return False
        if (
            turn.status != "RUNNING"
            or turn.locked_by != claim.worker_id
            or turn.attempts != claim.claim_attempt
        ):
            return False
        turn.locked_until = get_utcnow() + timedelta(seconds=self.setting.ai_worker_lease_seconds)
        await self.session.commit()
        return True

    async def find_turn_conversation_by_id(self, turn_id: str) -> tuple[ConversationTurn, Conversation]:
        return await self.turn_repo.find_turn_conversation_by_id(turn_id)

    def mark_superseded(self, turn: ConversationTurn, reason: str):
        turn.status = "SUPERSEDED"
        turn.last_error = reason
        turn.finished_at = get_utcnow()
        self._release_lease(turn)

    async def list_expired_running_turns_with_conversations(
        self,
    ) -> list[tuple[ConversationTurn, Conversation]]:
        """查询全部过期 Turn，并锁定各自所属会话。"""
        turns = await self.turn_repo.list_expired_running_turns(get_utcnow())
        result: list[tuple[ConversationTurn, Conversation]] = []
        for turn in turns:
            conv = await self.conver_repo.get_and_lock_by_id(turn.conversation_id)
            result.append((turn, conv))
        return result

    def retry_or_fail(self, turn: ConversationTurn, error: Exception) -> bool:
        """网络/服务故障在预算内重新排队，超过上限返回 False。"""
        turn.last_error = str(error)
        self._release_lease(turn)
        if turn.attempts < self.setting.ai_worker_max_attempts:
            turn.status = "COLLECTING"
            turn.collect_until = get_utcnow() + timedelta(
                seconds=self.setting.ai_worker_retry_delay_seconds
            )
            turn.run_id = None
            return True

        turn.status = "FAILED"
        turn.finished_at = get_utcnow()
        return False

    def requeue_for_recompute(self, turn: ConversationTurn, error: Exception) -> None:
        """同一输入上的流程版本冲突，允许一次有限重算。"""
        turn.flow_recompute_count += 1
        turn.last_error = str(error)
        turn.status = "COLLECTING"
        turn.collect_until = get_utcnow()
        turn.run_id = None
        self._release_lease(turn)

    @staticmethod
    def _release_lease(turn: ConversationTurn):
        turn.locked_by = None
        turn.locked_until = None

    def mark_completed(self, turn: ConversationTurn):
        turn.status = "COMPLETED"
        turn.finished_at = get_utcnow()
        self._release_lease(turn)

    def requeue(self, turn: ConversationTurn, error: Exception) -> None:
        """将租约超时的 Turn 重新放回待领取队列。"""
        turn.status = "COLLECTING"
        turn.collect_until = get_utcnow()
        turn.run_id = None
        turn.last_error = str(error)
        self._release_lease(turn)

    @staticmethod
    def _flow_snapshot(flow_state) -> FlowSnapshot:
        if flow_state is None:
            return FlowSnapshot(
                flow_id="",
                flow_type=None,
                node="INIT",
                slots={},
                pending_intents=[],
                clarification_attempts={},
                state_version=0,
                flow_definition_version=FLOW_DEFINITION_VERSION,
            )
        return FlowSnapshot(
            flow_id=flow_state.flow_id,
            flow_type=flow_state.flow_type,
            node=flow_state.node,
            slots={name: value for name, value in (flow_state.slots or {}).items()},
            pending_intents=list(flow_state.pending_intents or []),
            clarification_attempts=dict(flow_state.clarification_attempts or {}),
            state_version=flow_state.state_version,
            flow_definition_version=flow_state.flow_definition_version,
            last_applied_turn_id=flow_state.last_applied_turn_id,
        )
