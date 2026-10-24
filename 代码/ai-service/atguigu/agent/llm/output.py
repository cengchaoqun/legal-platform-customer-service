from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atguigu.agent.harness.rules.action import ActionCode


class ReplyType(StrEnum):
    """Agent 本轮生成的回复类型。"""

    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    DECLINE = "DECLINE"
    REQUEST_HANDOFF = "REQUEST_HANDOFF"


class HandoffReason(StrEnum):
    """转人工的业务原因。"""

    USER_REQUESTED = "USER_REQUESTED"
    COMPLAINT = "COMPLAINT"
    RISK_REVIEW = "RISK_REVIEW"
    LEGAL_ADVICE = "LEGAL_ADVICE"


class HandoffRequest(BaseModel):
    """定义 Customer Service 创建人工工单需要的信息。"""

    reason_code: HandoffReason = Field(description="创建人工工单使用的转人工原因")
    summary: str = Field(min_length=1, max_length=500, description="面向人工客服的问题摘要")


class PageActionRequest(BaseModel):
    """模型建议的页面动作；href 由服务端生成，模型不提供 URL。"""

    model_config = ConfigDict(extra="forbid")

    action_code: ActionCode = Field(description="服务端允许的页面动作编码")
    resource_id: str | None = Field(
        default=None,
        description="具体订单页面动作使用的订单编号，必须与订单详情工具成功确认的订单编号一致；订单列表页不提供",
    )

    @model_validator(mode="after")
    def validate_resource_id(self) -> Self:
        is_order_list = self.action_code == ActionCode.BROWSE_ORDERS
        if is_order_list and self.resource_id is not None:
            raise ValueError("BROWSE_ORDERS 不接受 resource_id")
        if not is_order_list and self.resource_id is None:
            raise ValueError(f"{self.action_code} 必须提供 resource_id")
        return self


class ClarificationOption(BaseModel):
    """澄清时提供的候选业务对象。

    只允许来自本次 Run 的成功查询结果，前端点击后发送 order / plan 对象消息。
    """

    model_config = ConfigDict(extra="forbid")

    object_type: Literal["order", "plan"]
    object_id: str = Field(min_length=2, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=200)


class AgentOutput(BaseModel):
    """定义大语言模型必须返回的结构化结果。"""

    reply_type: ReplyType = Field(description="本轮客服回复的业务类型")
    reply_content: str = Field(
        min_length=1, max_length=4000, description="可以直接展示给用户的完整回复内容"
    )
    handoff_request: HandoffRequest | None = Field(
        default=None, description="仅请求转人工时提供的工单信息"
    )
    page_action_request: PageActionRequest | None = Field(
        default=None,
        description="仅当用户明确要求前往页面办理时提供；仅查询业务信息时不得提供",
    )
    clarification_options: list[ClarificationOption] | None = Field(
        default=None,
        max_length=5,
        description="仅 CLARIFY 时提供，最多 5 个来自工具结果的候选对象",
    )
    clarification_slot: str | None = Field(
        default=None,
        max_length=60,
        description="仅 CLARIFY 时说明本次追问的槽位，例如 order_id 或 refund_reason",
    )

    @model_validator(mode="after")
    def validate_output_fields(self) -> Self:
        is_handoff = self.reply_type == ReplyType.REQUEST_HANDOFF

        if is_handoff and self.handoff_request is None:
            raise ValueError("REQUEST_HANDOFF 必须提供 handoff_request")
        if not is_handoff and self.handoff_request is not None:
            raise ValueError("只有 REQUEST_HANDOFF 可以提供 handoff_request")
        if self.page_action_request is not None and self.reply_type != ReplyType.ANSWER:
            raise ValueError("只有 ANSWER 可以提供 page_action_request")
        if self.clarification_options and self.reply_type != ReplyType.CLARIFY:
            raise ValueError("只有 CLARIFY 可以提供 clarification_options")
        if self.clarification_slot and self.reply_type != ReplyType.CLARIFY:
            raise ValueError("只有 CLARIFY 可以提供 clarification_slot")
        return self
