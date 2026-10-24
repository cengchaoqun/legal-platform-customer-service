import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atguigu_legal.models import (
    AfterSale,
    AfterSaleApplicationStatus,
    AfterSaleKind,
    Invoice,
    InvoiceStatus,
    Order,
    PaymentStatus,
    Plan,
    RefundStatus,
    ServiceProgressEvent,
    ServiceStatus,
)
from atguigu_legal.schemas import (
    AfterSaleProjection,
    InvoiceProjection,
    OrderDetail,
    OrderSummary,
    PlanDetail,
    PlanSummary,
    ServiceProgressOut,
)
from atguigu_legal.utils import get_utcnow

PAYMENT_LABELS = {
    PaymentStatus.UNPAID: "待支付",
    PaymentStatus.PAID: "已支付",
    PaymentStatus.REFUNDED: "已退款",
}

SERVICE_LABELS = {
    ServiceStatus.PENDING_START: "待开始",
    ServiceStatus.IN_SERVICE: "服务中",
    ServiceStatus.WAITING_MATERIALS: "材料待补充",
    ServiceStatus.DELIVERED: "已交付",
    ServiceStatus.COMPLETED: "已完成",
    ServiceStatus.CANCELLED: "已取消",
}

INVOICE_LABELS = {
    InvoiceStatus.NONE: "尚未申请开票",
    InvoiceStatus.SUBMITTED: "开票申请已受理",
    InvoiceStatus.PROCESSING: "开票处理中",
    InvoiceStatus.ISSUED: "发票已开具",
    InvoiceStatus.FAILED: "开票失败",
}

AFTER_SALE_APPLICATION_LABELS = {
    AfterSaleApplicationStatus.SUBMITTED: "申请已提交",
    AfterSaleApplicationStatus.REVIEWING: "审核中",
    AfterSaleApplicationStatus.APPROVED: "审核通过",
    AfterSaleApplicationStatus.REJECTED: "审核未通过",
}

REFUND_LABELS = {
    RefundStatus.NOT_REFUNDED: "尚未退款",
    RefundStatus.REFUNDING: "退款处理中",
    RefundStatus.REFUNDED: "已退款",
}

AFTER_SALE_KIND_LABELS = {
    AfterSaleKind.REFUND: "退款申请",
    AfterSaleKind.CANCEL: "取消申请",
    AfterSaleKind.COMPLAINT: "服务投诉",
}

OPEN_AFTER_SALE_STATUSES = (
    AfterSaleApplicationStatus.SUBMITTED,
    AfterSaleApplicationStatus.REVIEWING,
)


def money(value: Decimal | None) -> str | None:
    """金额统一按十进制字符串输出，避免前端用浮点计算退款额度。"""
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


class LegalBusinessService:
    """业务事实读取与受控写入。

    所有方法都要求调用方传入已经鉴权的 user_id，归属校验在服务端完成。
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ 读
    async def list_plans(self, query: str | None = None, service_type: str | None = None) -> list[PlanSummary]:
        statement = select(Plan).where(Plan.status == "ACTIVE")
        if query:
            keyword = f"%{query.strip()}%"
            statement = statement.where(
                Plan.name.ilike(keyword) | Plan.service_scope.ilike(keyword)
            )
        if service_type:
            statement = statement.where(Plan.service_type == service_type)
        plans = (await self.session.scalars(statement.order_by(Plan.price))).all()
        return [self._plan_summary(plan) for plan in plans]

    async def get_plan(self, plan_id: str) -> PlanDetail:
        plan = await self.session.get(Plan, plan_id)
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "套餐不存在")
        return PlanDetail(
            **self._plan_summary(plan).model_dump(),
            service_scope=plan.service_scope,
            updated_at=plan.updated_at,
        )

    async def list_orders(self, user_id: str) -> list[OrderSummary]:
        orders = (
            await self.session.scalars(
                select(Order)
                .where(Order.user_id == user_id)
                .options(selectinload(Order.plan))
                .order_by(Order.created_at.desc())
            )
        ).all()
        return [await self._order_summary(order) for order in orders]

    async def get_order(self, user_id: str, order_id: str) -> OrderDetail:
        order = await self._require_order(user_id, order_id)
        summary = await self._order_summary(order)
        invoice = await self._find_invoice(order.id)
        open_after_sale = await self._find_open_after_sale(order.id)
        return OrderDetail(
            **summary.model_dump(),
            plan_name=order.plan.name,
            service_type=order.plan.service_type,
            invoice_status=invoice.status if invoice else InvoiceStatus.NONE,
            open_after_sale_id=open_after_sale.id if open_after_sale else None,
        )

    async def get_service_progress(self, user_id: str, order_id: str) -> ServiceProgressOut:
        order = await self._require_order(user_id, order_id)
        events = (
            await self.session.scalars(
                select(ServiceProgressEvent)
                .where(ServiceProgressEvent.order_id == order.id)
                .order_by(ServiceProgressEvent.occurred_at)
            )
        ).all()
        current_node = events[-1].node if events else "订单已受理"
        return ServiceProgressOut(
            order_id=order.id,
            service_type=order.plan.service_type,
            service_name=order.service_name,
            current_node=current_node,
            service_status=order.service_status,
            missing_materials=list(order.missing_materials or []),
            estimated_at=order.estimated_at,
            events=[
                {
                    "node": event.node,
                    "status": event.status,
                    "time": event.occurred_at,
                    "description": event.description,
                }
                for event in events
            ],
            updated_at=order.updated_at,
        )

    async def get_invoice(self, user_id: str, order_id: str) -> InvoiceProjection:
        order = await self._require_order(user_id, order_id)
        invoice = await self._find_invoice(order.id)
        return self._invoice_projection(order, invoice)

    async def list_after_sales(
        self, user_id: str, order_id: str | None = None
    ) -> list[AfterSaleProjection]:
        statement = select(AfterSale).where(AfterSale.user_id == user_id)
        if order_id:
            statement = statement.where(AfterSale.order_id == order_id)
        records = (
            await self.session.scalars(statement.order_by(AfterSale.created_at.desc()))
        ).all()
        return [self._after_sale_projection(record) for record in records]

    # ------------------------------------------------------------------ 写
    async def cancel_order(
        self, user_id: str, order_id: str, *, idempotency_key: str, reason: str | None
    ) -> dict[str, Any]:
        async def execute() -> dict[str, Any]:
            order = await self._require_order(user_id, order_id)
            if order.service_status == ServiceStatus.CANCELLED:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "BUSINESS_STATE_CHANGED", "message": "订单已经取消"},
                )
            if order.service_status != ServiceStatus.PENDING_START:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "BUSINESS_STATE_CHANGED",
                        "message": "服务已经开始，不能直接取消，请申请售后由人工审核",
                    },
                )
            order.service_status = ServiceStatus.CANCELLED
            order.updated_at = get_utcnow()
            await self.session.flush()
            return {
                "order_id": order.id,
                "service_status": order.service_status,
                "service_status_label": SERVICE_LABELS[ServiceStatus.CANCELLED],
                "note": "订单已取消。如已支付，退款由平台按服务政策审核，演示环境不执行资金退款。",
            }

        return await self._run_idempotent(
            user_id, "cancel_order", idempotency_key, {"reason": reason}, execute
        )

    async def apply_invoice(
        self,
        user_id: str,
        order_id: str,
        *,
        idempotency_key: str,
        invoice_type: str,
        title_type: str,
        title: str,
        tax_id: str | None,
        email: str,
    ) -> dict[str, Any]:
        if title_type == "COMPANY" and not tax_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "FIELD_REQUIRED", "message": "企业抬头必须填写税号"},
            )

        async def execute() -> dict[str, Any]:
            order = await self._require_order(user_id, order_id)
            if order.payment_status != PaymentStatus.PAID:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "BUSINESS_STATE_CHANGED", "message": "订单未支付，无法申请开票"},
                )
            if order.service_status == ServiceStatus.CANCELLED:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "BUSINESS_STATE_CHANGED", "message": "订单已取消，无法申请开票"},
                )
            existing = await self._find_invoice(order.id)
            if existing is not None and existing.status == InvoiceStatus.ISSUED:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "BUSINESS_STATE_CHANGED", "message": "该订单已开具发票"},
                )
            if existing is not None and existing.status in (
                InvoiceStatus.SUBMITTED,
                InvoiceStatus.PROCESSING,
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "APPLICATION_IN_PROGRESS",
                        "message": "该订单已有处理中的开票申请",
                    },
                )

            invoice = existing or Invoice(
                order_id=order.id,
                amount=order.total_amount,
                currency=order.currency,
            )
            invoice.status = InvoiceStatus.SUBMITTED
            invoice.invoice_type = invoice_type
            invoice.title_type = title_type
            invoice.title = title
            invoice.tax_id = tax_id
            invoice.email = email
            invoice.failure_reason = None
            invoice.updated_at = get_utcnow()
            if existing is None:
                self.session.add(invoice)
            await self.session.flush()

            return {
                "invoice_id": invoice.id,
                "order_id": order.id,
                "status": invoice.status,
                "status_label": INVOICE_LABELS[invoice.status],
                "note": "开票申请已受理，尚未开具；开具进度可在订单详情查询。",
            }

        return await self._run_idempotent(
            user_id,
            "apply_invoice",
            idempotency_key,
            {
                "invoice_type": invoice_type,
                "title_type": title_type,
                "title": title,
                "tax_id": tax_id,
                "email": email,
            },
            execute,
        )

    async def apply_after_sale(
        self,
        user_id: str,
        order_id: str,
        *,
        idempotency_key: str,
        kind: str,
        reason: str,
        requested_amount: str | None,
    ) -> dict[str, Any]:
        amount = self._parse_amount(requested_amount)

        async def execute() -> dict[str, Any]:
            order = await self._require_order(user_id, order_id)
            if order.payment_status != PaymentStatus.PAID:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "BUSINESS_STATE_CHANGED", "message": "订单未支付，无法申请售后"},
                )
            if order.service_status == ServiceStatus.CANCELLED:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "BUSINESS_STATE_CHANGED", "message": "订单已取消"},
                )
            open_after_sale = await self._find_open_after_sale(order.id)
            if open_after_sale is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "APPLICATION_IN_PROGRESS",
                        "message": f"该订单已有处理中的售后申请 {open_after_sale.id}",
                    },
                )
            if amount is not None and amount > order.total_amount:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "AMOUNT_EXCEEDED",
                        "message": "申请金额不能超过订单实付金额",
                    },
                )

            record = AfterSale(
                order_id=order.id,
                user_id=user_id,
                kind=kind,
                reason=reason,
                requested_amount=amount,
                application_status=AfterSaleApplicationStatus.SUBMITTED,
                refund_status=RefundStatus.NOT_REFUNDED,
            )
            self.session.add(record)
            await self.session.flush()

            return {
                "after_sale_id": record.id,
                "order_id": order.id,
                "application_status": record.application_status,
                "application_status_label": AFTER_SALE_APPLICATION_LABELS[record.application_status],
                "refund_status": record.refund_status,
                "refund_status_label": REFUND_LABELS[record.refund_status],
                "note": "申请已提交，需人工审核；审核通过与退款到账是不同状态，请以后续进度为准。",
            }

        return await self._run_idempotent(
            user_id,
            "apply_after_sale",
            idempotency_key,
            {"kind": kind, "reason": reason, "requested_amount": requested_amount},
            execute,
        )

    async def admin_summary(self) -> dict[str, int]:
        plans = await self.session.scalar(select(func.count()).select_from(Plan))
        orders = await self.session.scalar(select(func.count()).select_from(Order))
        invoices = await self.session.scalar(select(func.count()).select_from(Invoice))
        after_sales = await self.session.scalar(select(func.count()).select_from(AfterSale))
        open_after_sales = await self.session.scalar(
            select(func.count())
            .select_from(AfterSale)
            .where(AfterSale.application_status.in_(OPEN_AFTER_SALE_STATUSES))
        )
        return {
            "plans": plans or 0,
            "orders": orders or 0,
            "invoices": invoices or 0,
            "after_sales": after_sales or 0,
            "open_after_sales": open_after_sales or 0,
        }

    # ------------------------------------------------------------- 内部工具
    async def _require_order(self, user_id: str, order_id: str) -> Order:
        order = await self.session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.plan))
        )
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
        if order.user_id != user_id:
            # 不区分“不存在”和“无权访问”以外的信息，避免泄露他人订单
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问该订单")
        return order

    async def _find_invoice(self, order_id: str) -> Invoice | None:
        return await self.session.scalar(select(Invoice).where(Invoice.order_id == order_id))

    async def _find_open_after_sale(self, order_id: str) -> AfterSale | None:
        return await self.session.scalar(
            select(AfterSale)
            .where(
                AfterSale.order_id == order_id,
                AfterSale.application_status.in_(OPEN_AFTER_SALE_STATUSES),
            )
            .order_by(AfterSale.created_at.desc())
        )

    async def _order_summary(self, order: Order) -> OrderSummary:
        invoice = await self._find_invoice(order.id)
        open_after_sale = await self._find_open_after_sale(order.id)
        has_after_sale = await self.session.scalar(
            select(func.count()).select_from(AfterSale).where(AfterSale.order_id == order.id)
        )
        return OrderSummary(
            id=order.id,
            plan_id=order.plan_id,
            service_name=order.service_name,
            total_amount=money(order.total_amount),
            currency=order.currency,
            payment_status=order.payment_status,
            service_status=order.service_status,
            payment_status_label=PAYMENT_LABELS.get(order.payment_status, order.payment_status),
            service_status_label=SERVICE_LABELS.get(order.service_status, order.service_status),
            allowed_actions=self._allowed_actions(
                order, invoice, open_after_sale, bool(has_after_sale)
            ),
            created_at=order.created_at,
            updated_at=order.updated_at,
        )

    @staticmethod
    def _allowed_actions(
        order: Order,
        invoice: Invoice | None,
        open_after_sale: AfterSale | None,
        has_after_sale: bool,
    ) -> list[str]:
        """页面按钮是否可用由业务状态决定，前端不能自行猜测。"""
        actions: list[str] = ["VIEW_SERVICE_PROGRESS", "CONTACT_SUPPORT"]
        if (
            order.payment_status == PaymentStatus.PAID
            and order.service_status == ServiceStatus.PENDING_START
        ):
            actions.append("CANCEL_ORDER")
        if (
            order.payment_status == PaymentStatus.PAID
            and order.service_status != ServiceStatus.CANCELLED
            and (invoice is None or invoice.status == InvoiceStatus.FAILED)
        ):
            actions.append("APPLY_INVOICE")
        if (
            order.payment_status == PaymentStatus.PAID
            and order.service_status != ServiceStatus.CANCELLED
            and open_after_sale is None
        ):
            actions.append("APPLY_AFTER_SALE")
        if has_after_sale:
            actions.append("VIEW_AFTER_SALES")
        return actions

    @staticmethod
    def _plan_summary(plan: Plan) -> PlanSummary:
        return PlanSummary(
            id=plan.id,
            name=plan.name,
            service_type=plan.service_type,
            price=money(plan.price) or "0.00",
            currency=plan.currency,
            rights=list(plan.rights or []),
            validity_description=plan.validity_description,
            status=plan.status,
            version=plan.version,
        )

    @staticmethod
    def _invoice_projection(order: Order, invoice: Invoice | None) -> InvoiceProjection:
        if invoice is None:
            return InvoiceProjection(
                order_id=order.id,
                status=InvoiceStatus.NONE,
                status_label=INVOICE_LABELS[InvoiceStatus.NONE],
                amount=money(order.total_amount) or "0.00",
                currency=order.currency,
            )
        return InvoiceProjection(
            id=invoice.id,
            order_id=order.id,
            status=invoice.status,
            status_label=INVOICE_LABELS.get(invoice.status, invoice.status),
            invoice_type=invoice.invoice_type,
            title_type=invoice.title_type,
            title=invoice.title,
            amount=money(invoice.amount) or "0.00",
            currency=invoice.currency,
            failure_reason=invoice.failure_reason,
            updated_at=invoice.updated_at,
        )

    @staticmethod
    def _after_sale_projection(record: AfterSale) -> AfterSaleProjection:
        return AfterSaleProjection(
            id=record.id,
            order_id=record.order_id,
            kind=record.kind,
            kind_label=AFTER_SALE_KIND_LABELS.get(record.kind, record.kind),
            reason=record.reason,
            application_status=record.application_status,
            application_status_label=AFTER_SALE_APPLICATION_LABELS.get(
                record.application_status, record.application_status
            ),
            refund_status=record.refund_status,
            refund_status_label=REFUND_LABELS.get(record.refund_status, record.refund_status),
            approved_amount=money(record.approved_amount),
            requested_amount=money(record.requested_amount),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _parse_amount(value: str | None) -> Decimal | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            amount = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "INVALID_AMOUNT", "message": "申请金额格式不正确"},
            ) from exc
        if amount <= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "INVALID_AMOUNT", "message": "申请金额必须大于 0"},
            )
        return amount.quantize(Decimal("0.01"))

    async def _run_idempotent(
        self,
        principal_id: str,
        operation: str,
        key: str,
        payload: dict[str, Any],
        execute: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """执行幂等写入。

        设计要点：
        1. 同一主体 + 同一操作 + 同一幂等键只执行一次；
        2. 载荷不同说明调用方复用了错误的键，必须拒绝而不是返回旧结果；
        3. 通过唯一约束兜底并发重复提交，冲突后回滚并读取已登记结果。
        """
        if not key or len(key) < 8:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "缺少有效的幂等键"},
            )

        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()

        existing = await self._find_idempotency(principal_id, operation, key)
        if existing is not None:
            return self._replay(existing, payload_hash)

        try:
            async with self.session.begin_nested():
                result = await execute()
                from atguigu_legal.models import IdempotencyRecord

                self.session.add(
                    IdempotencyRecord(
                        principal_id=principal_id,
                        operation=operation,
                        key=key,
                        payload_hash=payload_hash,
                        resource_id=str(
                            result.get("after_sale_id")
                            or result.get("invoice_id")
                            or result.get("order_id")
                            or ""
                        ),
                        response_snapshot=result,
                    )
                )
                await self.session.flush()
            await self.session.commit()
            return result
        except IntegrityError:
            # 并发重复请求：另一个事务已登记同一幂等键
            await self.session.rollback()
            existing = await self._find_idempotency(principal_id, operation, key)
            if existing is None:
                raise
            return self._replay(existing, payload_hash)

    async def _find_idempotency(self, principal_id: str, operation: str, key: str):
        from atguigu_legal.models import IdempotencyRecord

        return await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.principal_id == principal_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == key,
            )
        )

    @staticmethod
    def _replay(record, payload_hash: str) -> dict[str, Any]:
        if record.payload_hash != payload_hash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "相同幂等键被用于不同内容的请求，请核对原申请结果",
                },
            )
        snapshot = dict(record.response_snapshot)
        snapshot["idempotent_replay"] = True
        return snapshot
