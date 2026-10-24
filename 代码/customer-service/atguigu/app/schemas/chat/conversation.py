from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from atguigu.app.schemas.chat.flow import FlowSummary


class ConversationMode(StrEnum):
    AI = "AI"  # 用户端显示“AI 客服”
    QUEUED = "QUEUED"  # 用户端显示“等待人工”
    HUMAN = "HUMAN"  # 用户端显示“人工服务中”
    CLOSED = "CLOSED"  # 用户端显示“已结束”


class CurrentConversationResponse(BaseModel):
    id: str
    mode: ConversationMode
    is_processing: bool
    # 当前是否有开放工单，供前端显示人工入口状态
    handoff_status: Literal["waiting", "active", "resolved"] | None = None


class ConversationMessageResponse(BaseModel):
    message_id: str
    role: Literal["user", "ai", "human"]
    type: str = "text"
    content: dict[str, Any]
    created_at: datetime


class ConversationDetailResponse(BaseModel):
    """客服工作台会话详情。

    只返回授权范围内需要的交接上下文与流程摘要，不广播完整内部状态。
    """

    conversation_id: str
    mode: ConversationMode
    messages: list[ConversationMessageResponse]
    handoff_context: dict[str, Any] | None = None
    flow_summary: FlowSummary | None = None


class HandoffResolveRequest(BaseModel):
    """结束人工时提交的处理结论。"""

    resolution_note: str | None = Field(default=None, max_length=500)
    outcome: Literal["RESOLVED", "FOLLOW_UP_REQUIRED", "ESCALATED"] = "RESOLVED"
