"""模型决策与代码执行计划。

模型只输出 PolicyDecision（意图、槽位候选、下一步建议、澄清与升级原因）；
流程节点、工具白名单、缺失槽位和动作范围由服务端计算成 ExecutionPlan。
"""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atguigu.agent.harness.flows.contract import INTENTS, NEXT_ACTIONS


class PolicyIntent(StrEnum):
    PLAN_QUERY = "PLAN_QUERY"
    ORDER_QUERY = "ORDER_QUERY"
    INVOICE_QUERY = "INVOICE_QUERY"
    INVOICE_APPLY = "INVOICE_APPLY"
    SERVICE_PROGRESS_QUERY = "SERVICE_PROGRESS_QUERY"
    REFUND_APPLY = "REFUND_APPLY"
    AFTER_SALE_QUERY = "AFTER_SALE_QUERY"
    CANCEL_ORDER = "CANCEL_ORDER"
    POLICY_QUERY = "POLICY_QUERY"
    COMPLAINT = "COMPLAINT"
    HUMAN_TRANSFER = "HUMAN_TRANSFER"
    LEGAL_ADVICE_REQUEST = "LEGAL_ADVICE_REQUEST"
    UNKNOWN = "UNKNOWN"


class NextAction(StrEnum):
    QUERY_BUSINESS = "QUERY_BUSINESS"
    QUERY_POLICY = "QUERY_POLICY"
    ASK_CLARIFY = "ASK_CLARIFY"
    OFFER_PAGE_ACTION = "OFFER_PAGE_ACTION"
    ANSWER = "ANSWER"
    HANDOFF = "HANDOFF"
    DECLINE = "DECLINE"


class HandoffReasonCode(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    COMPLAINT = "COMPLAINT"
    RISK_REVIEW = "RISK_REVIEW"
    LEGAL_ADVICE = "LEGAL_ADVICE"


class SlotUpdate(BaseModel):
    """模型提出的槽位候选；验证状态由服务端根据工具结果决定。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=300)
    source_message_id: str | None = Field(default=None, max_length=80)
    confidence_note: str | None = Field(default=None, max_length=200)


class ConflictSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_name: str = Field(min_length=1, max_length=60)
    existing_value: str | None = Field(default=None, max_length=300)
    new_value: str | None = Field(default=None, max_length=300)
    reason: str = Field(min_length=1, max_length=200)


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_name: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=2, max_length=300)


class PolicyDecision(BaseModel):
    """模型的一次结构化决策。"""

    model_config = ConfigDict(extra="forbid")

    intents: list[PolicyIntent] = Field(min_length=1)
    primary_intent: PolicyIntent
    slot_updates: dict[str, SlotUpdate] = Field(default_factory=dict)
    conflicts: list[ConflictSignal] = Field(default_factory=list)
    proposed_next_action: NextAction
    clarification: ClarificationRequest | None = None
    handoff_reason: HandoffReasonCode | None = None
    handoff_summary: str | None = Field(default=None, max_length=300)
    # 仅用于不需要查询的简短说明，不得包含业务事实
    answer_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.primary_intent not in self.intents:
            raise ValueError("primary_intent 必须包含在 intents 中")
        if self.proposed_next_action == NextAction.ASK_CLARIFY and self.clarification is None:
            raise ValueError("ASK_CLARIFY 必须给出澄清问题")
        if self.proposed_next_action != NextAction.ASK_CLARIFY and self.clarification is not None:
            raise ValueError("只有 ASK_CLARIFY 可以携带 clarification")
        if self.proposed_next_action == NextAction.HANDOFF and self.handoff_reason is None:
            raise ValueError("HANDOFF 必须给出 handoff_reason")
        return self


class PlanBudget(BaseModel):
    """整轮预算由代码注入，模型不能填写或扩大。"""

    model_config = ConfigDict(frozen=True)

    max_model_calls: int = 3
    max_tool_calls: int = 8
    max_corrections: int = 1
    timeout_seconds: float = 45.0


class ExecutionPlan(BaseModel):
    """服务端根据决策与流程定义计算的执行计划。"""

    model_config = ConfigDict(extra="forbid")

    flow_type: str
    node: str
    selected_skill: str
    allowed_tool_names: list[str]
    required_slots: list[str]
    missing_slots: list[str]
    allowed_next_actions: list[str]
    target_resource: dict[str, str] = Field(default_factory=dict)
    mode: str = "EXECUTE"  # EXECUTE / DIRECT
    planned_action: str = "QUERY_BUSINESS"
    # 服务端计算的单一澄清槽位，避免一次追问多个问题
    clarify_slot: str | None = None
    # 本节点允许的页面动作编码，执行器据此校验模型给出的动作
    allowed_action_codes: list[str] = Field(default_factory=list)
    budget: PlanBudget = Field(default_factory=PlanBudget)
    reason: str = ""

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.flow_type not in INTENTS:
            raise ValueError("执行计划包含未知流程类型")
        for action in self.allowed_next_actions:
            if action not in NEXT_ACTIONS:
                raise ValueError("执行计划包含未知动作")
        return self

    def to_prompt_payload(self) -> dict[str, Any]:
        """提供给模型的只读计划摘要，不含预算与内部原因。"""
        return {
            "flow_type": self.flow_type,
            "node": self.node,
            "selected_skill": self.selected_skill,
            "allowed_tools": self.allowed_tool_names,
            "required_slots": self.required_slots,
            "missing_slots": self.missing_slots,
            "allowed_next_actions": self.allowed_next_actions,
            "target_resource": self.target_resource,
            "planned_action": self.planned_action,
            "allowed_action_codes": self.allowed_action_codes,
            "clarify_slot": self.clarify_slot,
        }
