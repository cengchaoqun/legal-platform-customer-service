from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolFailureType(StrEnum):
    """定义工具未返回成功结果的原因类型。"""

    BUSINESS = "BUSINESS"
    SERVICE_CALL = "SERVICE_CALL"
    CONTRACT = "CONTRACT"


class ToolFailureCode(StrEnum):
    """定义工具执行层生成的稳定失败结果码。"""

    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    INVALID_TOOL_RESULT = "INVALID_TOOL_RESULT"


class ToolResult[ResultData](BaseModel):
    """定义业务工具返回给 Agent 的统一结果。"""

    success: bool
    code: str
    message: str
    data: ResultData | None = None
    failure_type: ToolFailureType | None = None


class PlanData(BaseModel):
    """法律服务套餐摘要。"""

    id: str
    name: str
    service_type: str
    price: str
    currency: str
    rights: list[str]
    validity_description: str
    status: str
    version: str


class PlanDetailData(PlanData):
    service_scope: str
    updated_at: datetime


class OrderData(BaseModel):
    """订单事实；金额按十进制字符串传输，避免浮点误差。"""

    id: str
    plan_id: str
    service_name: str
    total_amount: str
    currency: str
    payment_status: str
    service_status: str
    payment_status_label: str | None = None
    service_status_label: str | None = None
    # 订单详情返回的开票状态摘要，避免为判断是否已开票额外调用工具
    invoice_status: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InvoiceData(BaseModel):
    """开票状态投影，不含完整税号与邮箱。"""

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


class ServiceProgressEventData(BaseModel):
    node: str
    status: str
    time: datetime
    description: str


class ServiceProgressData(BaseModel):
    order_id: str
    service_type: str
    service_name: str
    current_node: str
    service_status: str
    missing_materials: list[str] = Field(default_factory=list)
    estimated_at: datetime | None = None
    events: list[ServiceProgressEventData] = Field(default_factory=list)
    updated_at: datetime


class AfterSaleData(BaseModel):
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


def to_jsonable(payload: Any) -> Any:
    """把校验后的数据转换成模型可读的 JSON 结构。"""
    return payload
