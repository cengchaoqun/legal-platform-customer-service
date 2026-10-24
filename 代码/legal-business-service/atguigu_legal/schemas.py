from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolEnvelope(BaseModel):
    """业务服务统一返回外壳，与 AI 工具 ToolResult 契约一致。"""

    success: bool
    code: str
    message: str
    data: Any | None = None


class DemoTokenRequest(BaseModel):
    user_id: str = Field(min_length=2, max_length=40)
    role: Literal["customer", "agent", "admin", "harness"] = "customer"


class Principal(BaseModel):
    user_id: str
    role: str
    display_name: str
    membership: str | None = None


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    principal: Principal
    # 兼容原前端字段
    user_id: str
    role: str
    display_name: str


class DemoUserOut(BaseModel):
    id: str
    name: str
    role: str
    membership: str


class PlanSummary(BaseModel):
    id: str
    name: str
    service_type: str
    price: str
    currency: str
    rights: list[str]
    validity_description: str
    status: str
    version: str


class PlanDetail(PlanSummary):
    service_scope: str
    updated_at: datetime


class OrderSummary(BaseModel):
    id: str
    plan_id: str
    service_name: str
    total_amount: str
    currency: str
    payment_status: str
    service_status: str
    payment_status_label: str
    service_status_label: str
    allowed_actions: list[str]
    created_at: datetime
    updated_at: datetime


class OrderDetail(OrderSummary):
    plan_name: str
    service_type: str
    invoice_status: str
    open_after_sale_id: str | None = None


class ServiceProgressEventOut(BaseModel):
    node: str
    status: str
    time: datetime
    description: str


class ServiceProgressOut(BaseModel):
    order_id: str
    service_type: str
    service_name: str
    current_node: str
    service_status: str
    missing_materials: list[str]
    estimated_at: datetime | None = None
    events: list[ServiceProgressEventOut]
    updated_at: datetime


class InvoiceProjection(BaseModel):
    """给 AI 与页面的开票投影：不包含完整税号与邮箱。"""

    id: str | None = None
    order_id: str
    status: str
    status_label: str
    invoice_type: str | None = None
    title_type: str | None = None
    title: str | None = None
    amount: str
    currency: str
    failure_reason: str | None = None
    updated_at: datetime | None = None


class AfterSaleProjection(BaseModel):
    id: str
    order_id: str
    kind: str
    kind_label: str
    reason: str
    application_status: str
    application_status_label: str
    refund_status: str
    refund_status_label: str
    approved_amount: str | None = None
    requested_amount: str | None = None
    created_at: datetime
    updated_at: datetime


class InvoiceApplicationRequest(BaseModel):
    invoice_type: Literal["ELECTRONIC", "PAPER"] = "ELECTRONIC"
    title_type: Literal["PERSONAL", "COMPANY"] = "PERSONAL"
    title: str = Field(min_length=1, max_length=120)
    tax_id: str | None = Field(default=None, max_length=40)
    email: str = Field(min_length=3, max_length=160)


class AfterSaleApplicationRequest(BaseModel):
    kind: Literal["REFUND", "CANCEL", "COMPLAINT"] = "REFUND"
    reason: str = Field(min_length=2, max_length=500)
    requested_amount: str | None = Field(default=None, max_length=20)


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class AdminSummary(BaseModel):
    plans: int
    orders: int
    invoices: int
    after_sales: int
    open_after_sales: int
