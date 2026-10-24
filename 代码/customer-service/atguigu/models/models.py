from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text, BigInteger, Identity
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase

from atguigu.common.utils import get_utcnow, get_uid


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "uq_open_conversation_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("mode IN ('AI', 'QUEUED', 'HUMAN')")
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: get_uid("conv"))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="AI", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=get_utcnow, onupdate=get_utcnow
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_revision: Mapped[int] = mapped_column(Integer, default=0)
    answered_revision: Mapped[int] = mapped_column(Integer, default=0)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", lazy="selectin"
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        Index(
            "uq_collecting_turn_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'COLLECTING'"),
        ),
        Index(
            "uq_executing_turn_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: get_uid("turn"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="COLLECTING", index=True)
    start_revision: Mapped[int] = mapped_column(Integer)
    snapshot_revision: Mapped[int | None] = mapped_column(Integer)
    collect_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    max_collect_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(String(80), index=True)
    locked_by: Mapped[str | None] = mapped_column(String(120), index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 领取时记录的流程状态版本，结算时必须与当前 FlowState 版本一致
    base_flow_state_version: Mapped[int] = mapped_column(Integer, default=0)
    # 同一输入上流程版本冲突后的有限重算次数，避免无限循环
    flow_recompute_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_message_id"),
        UniqueConstraint(
            "conversation_id", "input_revision", name="uq_conversation_input_revision"
        ),
        UniqueConstraint("agent_run_id", "agent_outcome_seq", name="uq_agent_run_outcome_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    message_type: Mapped[str] = mapped_column(String(20), default="text")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_revision: Mapped[int | None] = mapped_column(Integer)
    agent_run_id: Mapped[str | None] = mapped_column(String(80), index=True)
    agent_outcome_seq: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class RealtimeOutbox(Base):
    """等待实时推送 Worker 发布的业务事件。"""

    __tablename__ = "realtime_outbox"

    id: Mapped[str] = mapped_column(
        String(80),
        primary_key=True,
        default=lambda: get_uid("evt"),
    )
    sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        unique=True,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(160), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=get_utcnow,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )


class Handoff(Base):
    """记录一次人工客服工单及其处理状态。"""

    __tablename__ = "handoffs"
    __table_args__ = (
        Index(
            "uq_open_handoff_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('waiting', 'active')"),
        ),
        Index(
            "uq_open_handoff_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('waiting', 'active')"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(80),
        primary_key=True,
        default=lambda: get_uid("handoff"),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20),
        default="waiting",
        index=True,
    )
    # 转人工原因与队列编码，供按队列分派；首版只有平台售后队列真实存在
    reason_code: Mapped[str | None] = mapped_column(String(40))
    queue_code: Mapped[str] = mapped_column(String(40), default="platform_aftersales")
    # 交接上下文：已验证槽位、证据引用、冲突、待办与安全检查点
    context_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 人工处理结论，结束人工时写入，供恢复时确认用户诉求
    resolution_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assigned_agent_id: Mapped[str | None] = mapped_column(
        String(40),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=get_utcnow,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class ConversationFlowState(Base):
    """跨轮业务流程状态。

    设计要点：
    1. 权威状态保存在 Customer 数据库，不随聊天历史裁剪或进程重启丢失；
    2. 每个会话只有一行当前活动流程，state_version 单调递增；
    3. 只有通过版本、租约与会话模式校验的候选补丁才能写入。
    """

    __tablename__ = "conversation_flow_states"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True
    )
    flow_id: Mapped[str] = mapped_column(String(80), default=lambda: get_uid("flow"))
    flow_type: Mapped[str | None] = mapped_column(String(40))
    node: Mapped[str] = mapped_column(String(40), default="INIT")
    slots: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    pending_intents: Mapped[list[str]] = mapped_column(JSONB, default=list)
    clarification_attempts: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    flow_definition_version: Mapped[str] = mapped_column(
        String(60), default="legal-support-flow-v1"
    )
    last_applied_turn_id: Mapped[str | None] = mapped_column(String(80))
    handoff_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=get_utcnow, onupdate=get_utcnow
    )
