"""流程契约镜像。

与 Customer Service 的 `app/schemas/chat/flow.py` 保持结构一致。
两个服务不共享 ORM 或包，靠 `tests/contract` 中的契约测试保证字段与枚举同步。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

FLOW_DEFINITION_VERSION = "legal-support-flow-v1"

INTENTS = (
    "PLAN_QUERY",
    "ORDER_QUERY",
    "INVOICE_QUERY",
    "INVOICE_APPLY",
    "SERVICE_PROGRESS_QUERY",
    "REFUND_APPLY",
    "AFTER_SALE_QUERY",
    "CANCEL_ORDER",
    "POLICY_QUERY",
    "COMPLAINT",
    "HUMAN_TRANSFER",
    "LEGAL_ADVICE_REQUEST",
    "UNKNOWN",
)

FLOW_NODES = (
    "INIT",
    "IDENTIFY_ORDER",
    "CHECK_ORDER",
    "CHECK_ELIGIBILITY",
    "COLLECT_REASON",
    "OFFER_APPLICATION",
    "WAIT_USER_SUBMISSION",
    "APPLICATION_SUBMITTED",
    "ANSWERED",
    "HANDED_OFF",
    "NEEDS_REVALIDATION",
)

NEXT_ACTIONS = (
    "QUERY_BUSINESS",
    "QUERY_POLICY",
    "ASK_CLARIFY",
    "OFFER_PAGE_ACTION",
    "ANSWER",
    "HANDOFF",
    "DECLINE",
)


class SlotSourceType(StrEnum):
    USER_MESSAGE = "USER_MESSAGE"
    OBJECT_MESSAGE = "OBJECT_MESSAGE"
    TOOL_RESULT = "TOOL_RESULT"
    HUMAN_RESOLUTION = "HUMAN_RESOLUTION"


class SlotValidationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"


class SlotValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(default=None, max_length=300)
    source_type: SlotSourceType = SlotSourceType.USER_MESSAGE
    source_message_id: str | None = Field(default=None, max_length=80)
    source_tool_call_id: str | None = Field(default=None, max_length=120)
    validation_status: SlotValidationStatus = SlotValidationStatus.UNVERIFIED
    updated_at: datetime | None = None
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=5)


class FlowSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str
    flow_type: str | None = None
    node: str = "INIT"
    slots: dict[str, SlotValue] = Field(default_factory=dict)
    pending_intents: list[str] = Field(default_factory=list)
    clarification_attempts: dict[str, int] = Field(default_factory=dict)
    state_version: int = 0
    flow_definition_version: str = FLOW_DEFINITION_VERSION
    last_applied_turn_id: str | None = None

    @model_validator(mode="after")
    def validate_enums(self) -> "FlowSnapshot":
        if self.flow_type is not None and self.flow_type not in INTENTS:
            raise ValueError(f"未知流程类型：{self.flow_type}")
        if self.node not in FLOW_NODES:
            raise ValueError(f"未知流程节点：{self.node}")
        return self


class FlowPatchChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_type: str | None = None
    node: str | None = None
    slots: dict[str, SlotValue] = Field(default_factory=dict)
    pending_intents: list[str] | None = None
    clarification_attempts: dict[str, int] | None = None
    reset_flow: bool = False

    @model_validator(mode="after")
    def validate_whitelist(self) -> "FlowPatchChanges":
        if self.flow_type is not None and self.flow_type not in INTENTS:
            raise ValueError(f"补丁包含未知流程类型：{self.flow_type}")
        if self.node is not None and self.node not in FLOW_NODES:
            raise ValueError(f"补丁包含未知流程节点：{self.node}")
        for intent in self.pending_intents or []:
            if intent not in INTENTS:
                raise ValueError(f"补丁包含未知待办意图：{intent}")
        return self


class FlowPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: str = Field(min_length=1, max_length=120)
    base_state_version: int = Field(ge=0)
    turn_id: str = Field(min_length=1, max_length=80)
    flow_definition_version: str = Field(min_length=1, max_length=60)
    changes: FlowPatchChanges
