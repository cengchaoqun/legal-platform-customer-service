from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atguigu.app.schemas.chat.conversation import ConversationMode

# 文本消息长度上限：客服对话不需要长文，过长内容应通过工单或材料页面提交
MAX_TEXT_LENGTH = 2000


class MessageType(StrEnum):
    TEXT = "text"
    OBJECT = "object"


class MessageRole(StrEnum):
    USER = "user"
    AI = "ai"
    HUMAN = "human"


class TextContent(BaseModel):
    """文本消息内容：只允许 text 字段。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class ObjectContent(BaseModel):
    """对象消息只携带类型与 ID，价格和状态由后端重新查询。"""

    model_config = ConfigDict(extra="forbid")

    object_type: Literal["order", "plan"]
    object_id: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    query_text: str | None = Field(default=None, max_length=200)


class ChatMessageRequest(BaseModel):
    message_id: str = Field(min_length=4, max_length=80, pattern=r"^[A-Za-z0-9_:.\-]+$")
    type: MessageType
    content: dict[str, Any]

    @model_validator(mode="after")
    def validate_content_by_type(self) -> Self:
        """按消息类型做字段级校验，未知字段与超长内容直接拒绝。"""
        if self.type == MessageType.TEXT:
            parsed = TextContent.model_validate(self.content)
            self.content = parsed.model_dump()
        else:
            parsed = ObjectContent.model_validate(self.content)
            self.content = parsed.model_dump(exclude_none=True)
        return self


class AcceptUserMessageResponse(BaseModel):
    """用户消息受理结果。"""

    conversation_id: str
    mode: ConversationMode
    # 是否由前置规则直接创建了人工工单（不依赖模型可用性）
    handoff_created: bool = False


class HistoryMessageResponse(BaseModel):
    """聊天历史中的消息及会话开始时间。"""
    sequence: int
    message_id: str
    conversation_id: str
    role: MessageRole
    type: MessageType
    content: dict[str, Any]
    created_at: datetime
    conversation_started_at: datetime
