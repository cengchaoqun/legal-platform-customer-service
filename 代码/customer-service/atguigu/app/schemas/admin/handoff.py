from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HandoffReplyRequest(BaseModel):
    """人工客服发送的文本回复。"""

    message_id: str = Field(min_length=4, max_length=80)
    text: str = Field(min_length=1, max_length=4000)


class OpenHandoffResponse(BaseModel):
    """客服工作台中的开放工单。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    user_id: str
    summary: str
    status: Literal["waiting", "active"]
    reason_code: str | None = None
    queue_code: str
    assigned_agent_id: str | None
    created_at: datetime
    # 队列页只展示精简上下文；完整交接内容由授权会话详情接口返回
    context_preview: dict[str, Any] | None = None
