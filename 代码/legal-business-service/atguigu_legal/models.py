from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from atguigu_legal.utils import get_uid, get_utcnow


class Base(DeclarativeBase):
    pass


class PaymentStatus(StrEnum):
    """订单支付状态，与履约状态分开表达。"""

    UNPAID = "UNPAID"
    PAID = "PAID"
    REFUNDED = "REFUNDED"


class ServiceStatus(StrEnum):
    """法律服务履约状态；节点推进只由业务方写入，AI 不得自行推进。"""

    PENDING_START = "PENDING_START"
    IN_SERVICE = "IN_SERVICE"
    WAITING_MATERIALS = "WAITING_MATERIALS"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class InvoiceStatus(StrEnum):
    NONE = "NONE"
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    ISSUED = "ISSUED"
    FAILED = "FAILED"


class AfterSaleKind(StrEnum):
    REFUND = "REFUND"
    CANCEL = "CANCEL"
    COMPLAINT = "COMPLAINT"


class AfterSaleApplicationStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RefundStatus(StrEnum):
    """退款资金状态独立于申请状态：申请通过不等于款项到账。"""

    NOT_REFUNDED = "NOT_REFUNDED"
    REFUNDING = "REFUNDING"
    REFUNDED = "REFUNDED"


class DemoUser(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20), default="customer", index=True)
    membership: Mapped[str] = mapped_column(String(60), default="普通会员")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)


class Plan(Base):
    """法律服务套餐：价格与权益是业务事实，模型只能引用工具结果。"""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: get_uid("plan"))
    name: Mapped[str] = mapped_column(String(120), index=True)
    service_type: Mapped[str] = mapped_column(String(40), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    rights: Mapped[list[str]] = mapped_column(JSONB, default=list)
    service_scope: Mapped[str] = mapped_column(Text, default="")
    validity_description: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    version: Mapped[str] = mapped_column(String(40), default="plan-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=get_utcnow, onupdate=get_utcnow
    )


class Order(Base):
    """订单归属由服务端按可信身份校验，订单号本身不是身份证明。"""

    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), index=True)
    service_name: Mapped[str] = mapped_column(String(160))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    payment_status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PAID)
    service_status: Mapped[str] = mapped_column(
        String(30), default=ServiceStatus.PENDING_START, index=True
    )
    # 待补材料与预计完成时间都由业务方维护；没有预计时间时保持为空，不自行推断
    missing_materials: Mapped[list[str]] = mapped_column(JSONB, default=list)
    estimated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=get_utcnow, onupdate=get_utcnow
    )

    plan: Mapped[Plan] = relationship(lazy="joined")


class ServiceProgressEvent(Base):
    """履约节点事件，按时间排序展示；预计时间仅在业务方提供时存在。"""

    __tablename__ = "service_progress_events"
    __table_args__ = (Index("ix_service_progress_order_time", "order_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: get_uid("sp"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    node: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    description: Mapped[str] = mapped_column(Text, default="")


class Invoice(Base):
    """每个订单至多一条开票记录；详细税号与邮箱不回传给模型。"""

    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("order_id", name="uq_invoice_order"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: get_uid("inv"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.SUBMITTED, index=True)
    invoice_type: Mapped[str] = mapped_column(String(30), default="ELECTRONIC")
    title_type: Mapped[str] = mapped_column(String(20), default="PERSONAL")
    title: Mapped[str] = mapped_column(String(160), default="")
    # 敏感字段只保存在业务库，工具投影不返回
    tax_id: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=get_utcnow, onupdate=get_utcnow
    )


class AfterSale(Base):
    """售后/退款申请。申请状态、审核状态与资金状态分别记录。"""

    __tablename__ = "after_sales"
    __table_args__ = (Index("ix_after_sales_order", "order_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: get_uid("as"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), default=AfterSaleKind.REFUND)
    reason: Mapped[str] = mapped_column(Text)
    requested_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    application_status: Mapped[str] = mapped_column(
        String(20), default=AfterSaleApplicationStatus.SUBMITTED
    )
    refund_status: Mapped[str] = mapped_column(String(20), default=RefundStatus.NOT_REFUNDED)
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=get_utcnow, onupdate=get_utcnow
    )


class IdempotencyRecord(Base):
    """写请求幂等登记：同键同载荷返回原结果，同键不同载荷拒绝。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("principal_id", "operation", "key", name="uq_idempotency_scope"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: get_uid("idem"))
    principal_id: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(60))
    key: Mapped[str] = mapped_column(String(120))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    resource_id: Mapped[str] = mapped_column(String(60))
    response_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utcnow)
