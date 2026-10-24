from typing import Any

from atguigu.agent.harness.run.executor import ExecutionOutcome
from atguigu.agent.llm.output import ReplyType
from atguigu.common.config import get_settings
from atguigu.common.utils import get_uid
from atguigu.models.models import AgentRunState


class AgentRunOutPutMapper:
    """把执行结果映射为 AgentRun 状态与对外事件数据。"""

    @staticmethod
    def map(outcome: ExecutionOutcome) -> tuple[AgentRunState, dict[str, Any]]:
        validated = outcome.validated_output

        if validated.reply_type == ReplyType.REQUEST_HANDOFF:
            return AgentRunState.HANDED_OFF, AgentRunOutPutMapper._build_handoff_output(outcome)

        state = (
            AgentRunState.DECISION_PREPARED
            if validated.page_action is not None
            else AgentRunState.COMPLETED
        )
        return state, AgentRunOutPutMapper._build_message_output(outcome)

    @classmethod
    def _build_message_output(cls, outcome: ExecutionOutcome) -> dict[str, Any]:
        validated = outcome.validated_output
        content: dict[str, Any] = {
            "text": validated.reply_content,
            "reply_type": validated.reply_type.value,
        }
        if validated.page_action is not None:
            content["action"] = validated.page_action.model_dump(mode="json")
        if validated.clarification_options:
            content["choices"] = [
                option.model_dump(mode="json")
                for option in validated.clarification_options
            ]

        return {
            "message_id": get_uid("msg"),
            "content": content,
            "flow_update": outcome.patch.model_dump(mode="json"),
            "plan": outcome.plan.to_prompt_payload(),
            "clarify_slot": outcome.plan.clarify_slot,
        }

    @classmethod
    def _build_handoff_output(cls, outcome: ExecutionOutcome) -> dict[str, Any]:
        settings = get_settings()
        validated = outcome.validated_output
        reason_code = validated.handoff_request.reason_code.value
        queue_code = "platform_aftersales"
        if reason_code == "LEGAL_ADVICE" and settings.professional_review_available:
            # 只有真实配置了专业承接队列时才进入该队列
            queue_code = "professional_review"

        return {
            "reason_code": reason_code,
            "queue_code": queue_code,
            "summary": validated.handoff_request.summary,
            "message": validated.reply_content,
            "handoff_context": cls._build_handoff_context(outcome, reason_code),
            "flow_update": outcome.patch.model_dump(mode="json"),
            "plan": outcome.plan.to_prompt_payload(),
        }

    @staticmethod
    def _build_handoff_context(outcome: ExecutionOutcome, reason_code: str) -> dict[str, Any]:
        plan = outcome.plan
        decision = outcome.decision
        changes = outcome.patch.changes
        slots = changes.slots or {}

        verified: dict[str, str] = {}
        unverified: dict[str, str] = {}
        for name, slot in slots.items():
            value = slot.value if hasattr(slot, "value") else slot.get("value")
            status = (
                slot.validation_status
                if hasattr(slot, "validation_status")
                else slot.get("validation_status")
            )
            if value is None:
                continue
            if str(status) in ("VERIFIED", "SlotValidationStatus.VERIFIED"):
                verified[name] = str(value)
            else:
                unverified[name] = str(value)

        attempted = list(getattr(outcome.evidence, "successful_tools", []) or [])
        return {
            "intent": plan.flow_type,
            "primary_intent": decision.primary_intent.value,
            "summary": outcome.validated_output.handoff_request.summary,
            "order_id": verified.get("order_id") or plan.target_resource.get("order_id"),
            "after_sale_id": verified.get("after_sale_id"),
            "verified_slots": verified,
            "unverified_slots": unverified,
            "conflicts": [item.model_dump() for item in decision.conflicts],
            "pending_intents": list(changes.pending_intents or []),
            "attempted_steps": [f"tool:{name}" for name in attempted]
            or ["policy:handoff_requested"],
            "failure_reason": reason_code,
            "flow_node": changes.node,
            "flow_state_version": outcome.patch.base_state_version,
            "source_message_ids": [
                update.source_message_id
                for update in decision.slot_updates.values()
                if update.source_message_id
            ],
        }


class ValidationSummaryBuilder:
    """生成管理端展示的校验摘要，避免回传完整工具报文。"""

    @staticmethod
    def build(outcome: ExecutionOutcome) -> dict[str, Any]:
        validated = outcome.validated_output
        return {
            "passed": True,
            "reply_type": validated.reply_type.value,
            "action_code": (
                validated.page_action.action_code if validated.page_action else None
            ),
            "plan": outcome.plan.to_prompt_payload(),
            "reason": outcome.plan.reason,
            "runtime_mode": outcome.runtime_mode,
            "tool_calls": list(getattr(outcome.evidence, "successful_tools", []) or []),
        }
