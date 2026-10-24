"""跨轮流程状态的镜像契约。

Customer 与 AI Service 各有一份结构相同的 Schema，靠契约测试保证一致；
两个服务不共享 ORM 与数据库会话。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FLOW_DEFINITION_VERSION = "legal-support-flow-v1"

# 首版意图集合，与 AI Service 的 PolicyDecision 保持一致
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

# 流程节点；HANDED_OFF 与 NEEDS_REVALIDATION 用于人工暂停与恢复
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
    """槽位值、来源与验证状态分开保存，便于冲突审计。"""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(default=None, max_length=300)
    source_type: SlotSourceType = SlotSourceType.USER_MESSAGE
    source_message_id: str | None = Field(default=None, max_length=80)
    source_tool_call_id: str | None = Field(default=None, max_length=120)
    validation_status: SlotValidationStatus = SlotValidationStatus.UNVERIFIED
    updated_at: datetime | None = None
    # 保留最近若干次取值，改口时仍可追溯
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=5)


class FlowSnapshot(BaseModel):
    """发送给 AI Service 的只读流程快照。"""

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
    """允许 AI 提交的白名单变更字段。"""

    model_config = ConfigDict(extra="forbid")

    flow_type: str | None = None
    node: str | None = None
    slots: dict[str, SlotValue] = Field(default_factory=dict)
    pending_intents: list[str] | None = None
    clarification_attempts: dict[str, int] | None = None
    # 用户已不需要继续办理时清空活动流程
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
    """AI 返回的候选状态补丁，不能是任意 JSON 或可执行脚本。"""

    model_config = ConfigDict(extra="forbid")

    patch_id: str = Field(min_length=1, max_length=120)
    base_state_version: int = Field(ge=0)
    turn_id: str = Field(min_length=1, max_length=80)
    flow_definition_version: str = Field(min_length=1, max_length=60)
    changes: FlowPatchChanges


class FlowSummary(BaseModel):
    """对外只暴露必要摘要，不广播内部槽位细节。"""

    flow_type: str | None
    node: str
    state_version: int
    has_active_flow: bool
    updated_at: datetime | None = None


class HandoffContextSnapshot(BaseModel):
    """交接给人工客服的结构化上下文。"""

    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    primary_intent: str | None = None
    summary: str = ""
    order_id: str | None = None
    after_sale_id: str | None = None
    verified_slots: dict[str, str] = Field(default_factory=dict)
    unverified_slots: dict[str, str] = Field(default_factory=dict)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    pending_intents: list[str] = Field(default_factory=list)
    attempted_steps: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    flow_node: str | None = None
    flow_state_version: int | None = None
    source_message_ids: list[str] = Field(default_factory=list)
