from typing import Any

from atguigu.app.schemas.chat.flow import (
    FLOW_DEFINITION_VERSION,
    FlowPatch,
    FlowSnapshot,
    FlowSummary,
    SlotValue,
)
from atguigu.app.respositories.chat.flow import FlowStateRepository
from atguigu.common.utils import get_uid, get_utcnow
from atguigu.models.models import ConversationFlowState


class FlowVersionConflict(RuntimeError):
    """候选补丁基于的流程版本已经过期。"""

    code = "FLOW_VERSION_CONFLICT"


class FlowPatchInvalid(ValueError):
    """候选补丁不符合白名单或契约规则。"""

    code = "FLOW_PATCH_INVALID"


class FlowStateService:
    """跨轮流程状态的读写与版本控制。

    该类不执行模型调用，也不访问业务 HTTP；只负责状态快照、补丁校验与持久化。
    调用方必须已经持有会话级锁，并在同一事务中完成消息、工单与 Outbox 的写入。
    """

    def __init__(self, session):
        self.session = session
        self.repository = FlowStateRepository(session)

    # ------------------------------------------------------------------ 读
    async def load_snapshot(self, conversation_id: str) -> FlowSnapshot:
        state = await self.repository.find_by_conversation_id(conversation_id)
        return self._to_snapshot(state)

    async def lock_state(self, conversation_id: str) -> ConversationFlowState | None:
        """结算事务中锁定流程状态行，保持 Conversation -> Turn -> Flow -> Handoff 的锁序。"""
        return await self.repository.lock_by_conversation_id(conversation_id)

    async def get_summary(self, conversation_id: str) -> FlowSummary | None:
        state = await self.repository.find_by_conversation_id(conversation_id)
        if state is None:
            return None
        return FlowSummary(
            flow_type=state.flow_type,
            node=state.node,
            state_version=state.state_version,
            has_active_flow=state.flow_type is not None,
            updated_at=state.updated_at,
        )

    # ------------------------------------------------------------------ 写
    async def apply_patch_locked(
        self,
        conversation_id: str,
        state: ConversationFlowState | None,
        patch: FlowPatch,
        *,
        expected_version: int,
        turn_id: str,
    ) -> FlowSnapshot:
        """在持有结算事务与有效租约时应用候选补丁。

        校验顺序：
        1. 流程定义版本匹配；
        2. 补丁基准版本 = 领取快照版本 = 当前状态版本；
        3. 补丁关联的 Turn 必须是当前结算 Turn。
        """
        if patch.flow_definition_version != FLOW_DEFINITION_VERSION:
            raise FlowPatchInvalid(
                f"不支持的流程定义版本：{patch.flow_definition_version}"
            )
        if patch.turn_id != turn_id:
            raise FlowPatchInvalid("补丁关联的 Turn 与当前结算 Turn 不一致")

        current_version = state.state_version if state is not None else 0
        if patch.base_state_version != expected_version or current_version != expected_version:
            raise FlowVersionConflict(
                f"流程版本冲突：base={patch.base_state_version}, "
                f"expected={expected_version}, current={current_version}"
            )

        if state is None:
            state = self._new_state(conversation_id)

        self._apply_changes(state, patch.changes)
        state.state_version = current_version + 1
        state.last_applied_turn_id = turn_id
        state.flow_definition_version = FLOW_DEFINITION_VERSION
        state.updated_at = get_utcnow()
        await self.session.flush()
        return self._to_snapshot(state)

    async def pause_for_handoff(
        self,
        conversation_id: str,
        *,
        context: dict[str, Any],
        turn_id: str | None = None,
    ) -> FlowSummary | None:
        """记录暂停检查点并递增版本，避免人工期间旧补丁仍可写入。"""
        state = await self.repository.lock_by_conversation_id(conversation_id)
        if state is None:
            return None
        state.handoff_checkpoint = {
            **(state.handoff_checkpoint or {}),
            "paused_at": get_utcnow().isoformat(),
            "node_before_pause": state.node,
            "context": context,
        }
        state.node = "HANDED_OFF"
        state.state_version += 1
        if turn_id:
            state.last_applied_turn_id = turn_id
        state.updated_at = get_utcnow()
        await self.session.flush()
        return await self.get_summary(conversation_id)

    async def prepare_resume(
        self, conversation_id: str, *, resolution: dict[str, Any]
    ) -> FlowSummary | None:
        """人工结束后进入 NEEDS_REVALIDATION，不直接复用旧资格。

        若会话已经没有活动流程，则不做任何恢复动作。
        """
        state = await self.repository.lock_by_conversation_id(conversation_id)
        if state is None or state.flow_type is None:
            return None
        checkpoint = dict(state.handoff_checkpoint or {})
        checkpoint["resolution"] = resolution
        checkpoint["resumed_at"] = get_utcnow().isoformat()
        if state.node != "APPLICATION_SUBMITTED":
            state.node = "NEEDS_REVALIDATION"
        state.handoff_checkpoint = checkpoint
        state.state_version += 1
        state.updated_at = get_utcnow()
        await self.session.flush()
        return await self.get_summary(conversation_id)

    async def close_flow(self, conversation_id: str) -> None:
        """清空活动流程（用户明确表示不再办理时调用）。"""
        state = await self.repository.lock_by_conversation_id(conversation_id)
        if state is None:
            return
        self._reset(state)
        state.state_version += 1
        state.updated_at = get_utcnow()
        await self.session.flush()

    # --------------------------------------------------------------- 内部
    def _new_state(self, conversation_id: str) -> ConversationFlowState:
        state = ConversationFlowState(
            conversation_id=conversation_id,
            flow_id=get_uid("flow"),
            node="INIT",
            slots={},
            pending_intents=[],
            clarification_attempts={},
            state_version=0,
            flow_definition_version=FLOW_DEFINITION_VERSION,
        )
        self.repository.add(state)
        return state

    def _apply_changes(self, state: ConversationFlowState, changes) -> None:
        if changes.reset_flow:
            self._reset(state)
            return

        if changes.flow_type is not None and changes.flow_type != state.flow_type:
            # 切换流程类型时清空上一流程的槽位，避免把退款资格带到开票流程
            state.slots = {}
            state.clarification_attempts = {}
            state.flow_type = changes.flow_type
            state.node = "INIT"
        elif changes.flow_type is not None:
            state.flow_type = changes.flow_type

        if changes.slots:
            self._merge_slots(state, changes.slots)

        if changes.node is not None:
            state.node = changes.node
        if changes.pending_intents is not None:
            state.pending_intents = list(dict.fromkeys(changes.pending_intents))
        if changes.clarification_attempts is not None:
            state.clarification_attempts = {
                key: int(value)
                for key, value in changes.clarification_attempts.items()
                if int(value) >= 0
            }

    @staticmethod
    def _reset(state: ConversationFlowState) -> None:
        state.flow_type = None
        state.node = "INIT"
        state.slots = {}
        state.pending_intents = []
        state.clarification_attempts = {}

    @staticmethod
    def _merge_slots(state: ConversationFlowState, slots: dict[str, SlotValue]) -> None:
        merged = dict(state.slots or {})
        now = get_utcnow()
        order_switched = False

        for name, slot in slots.items():
            previous = merged.get(name)
            previous_value = previous.get("value") if isinstance(previous, dict) else None
            if name == "order_id" and previous_value and slot.value and previous_value != slot.value:
                order_switched = True

            payload = slot.model_dump(mode="json")
            history = list(previous.get("history", [])) if isinstance(previous, dict) else []
            if previous_value is not None:
                history.append(
                    {
                        "value": previous_value,
                        "validation_status": previous.get("validation_status"),
                        "updated_at": previous.get("updated_at"),
                    }
                )
            payload["history"] = history[-5:]
            payload["updated_at"] = now.isoformat()
            merged[name] = payload

        if order_switched:
            # 目标订单变化后，依赖旧订单的退款原因与资格全部失效
            for dependent in ("refund_reason", "requested_amount", "invoice_type", "eligibility"):
                merged.pop(dependent, None)

        state.slots = merged

    @staticmethod
    def _to_snapshot(state: ConversationFlowState | None) -> FlowSnapshot:
        if state is None:
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
            flow_id=state.flow_id,
            flow_type=state.flow_type,
            node=state.node,
            slots={
                name: SlotValue.model_validate(value)
                for name, value in (state.slots or {}).items()
            },
            pending_intents=list(state.pending_intents or []),
            clarification_attempts=dict(state.clarification_attempts or {}),
            state_version=state.state_version,
            flow_definition_version=state.flow_definition_version,
            last_applied_turn_id=state.last_applied_turn_id,
        )
