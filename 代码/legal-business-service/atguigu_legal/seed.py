from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu_legal.models import (
    AfterSale,
    AfterSaleApplicationStatus,
    AfterSaleKind,
    DemoUser,
    Invoice,
    InvoiceStatus,
    Order,
    PaymentStatus,
    Plan,
    RefundStatus,
    ServiceProgressEvent,
    ServiceStatus,
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


async def seed_demo_data(session: AsyncSession) -> None:
    """导入演示数据。

    仅当用户表为空时导入，避免覆盖已存在的业务数据；重复执行是安全的。
    """
    existing = await session.scalar(select(func.count()).select_from(DemoUser))
    if existing:
        return

    session.add_all(
        [
            DemoUser(id="user_001", display_name="张岚", role="customer", membership="合同审核会员"),
            DemoUser(id="user_002", display_name="李默", role="customer", membership="法律咨询会员"),
            DemoUser(id="agent_001", display_name="客服小林", role="agent"),
            DemoUser(id="agent_002", display_name="客服小周", role="agent"),
            DemoUser(id="admin_001", display_name="系统管理员", role="admin"),
        ]
    )

    plans = {
        "PLAN_CONTRACT_REVIEW": Plan(
            id="PLAN_CONTRACT_REVIEW",
            name="合同审核服务",
            service_type="CONTRACT_REVIEW",
            price=Decimal("299.00"),
            rights=[
                "1 份合同全文审阅",
                "书面修改建议",
                "1 次补充问答",
            ],
            service_scope="适用于劳动合同、采购合同、服务合同的条款审阅与风险提示。",
            validity_description="自购买之日起 30 天内提交审核",
            version="plan-2026-10",
        ),
        "PLAN_LEGAL_CONSULT": Plan(
            id="PLAN_LEGAL_CONSULT",
            name="法律咨询服务",
            service_type="LEGAL_CONSULT",
            price=Decimal("199.00"),
            rights=[
                "30 分钟一对一咨询",
                "咨询要点记录",
            ],
            service_scope="适用于日常法律问题的初步说明与处理方向建议。",
            validity_description="自购买之日起 15 天内预约",
            version="plan-2026-10",
        ),
        "PLAN_DOC_DRAFT": Plan(
            id="PLAN_DOC_DRAFT",
            name="法律文书起草",
            service_type="DOC_DRAFT",
            price=Decimal("599.00"),
            rights=[
                "1 份法律文书初稿",
                "1 次修改",
            ],
            service_scope="适用于律师函、催告函、简单协议的起草。",
            validity_description="自购买之日起 45 天内提交需求",
            version="plan-2026-10",
        ),
        "PLAN_ANNUAL_ADVISOR": Plan(
            id="PLAN_ANNUAL_ADVISOR",
            name="企业常法顾问",
            service_type="ANNUAL_ADVISOR",
            price=Decimal("3980.00"),
            rights=[
                "全年不限次书面咨询",
                "每月 1 次合同审核",
                "季度合规提示",
            ],
            service_scope="适用于小微企业的日常经营法律支持，不含诉讼代理。",
            validity_description="自开通之日起 12 个月",
            version="plan-2026-10",
        ),
    }
    session.add_all(plans.values())
    # 先落库套餐，保证订单外键可见；随后按依赖顺序刷新
    await session.flush()

    orders = [
        Order(
            id="ORDER_001",
            user_id="user_001",
            plan_id="PLAN_CONTRACT_REVIEW",
            service_name="合同审核服务",
            total_amount=Decimal("299.00"),
            payment_status=PaymentStatus.PAID,
            service_status=ServiceStatus.WAITING_MATERIALS,
            missing_materials=["合同原件或清晰扫描件", "对方主体信息"],
            created_at=_at(20, 10, 30),
            updated_at=_at(21, 9, 20),
        ),
        Order(
            id="ORDER_002",
            user_id="user_001",
            plan_id="PLAN_LEGAL_CONSULT",
            service_name="法律咨询服务",
            total_amount=Decimal("199.00"),
            payment_status=PaymentStatus.PAID,
            service_status=ServiceStatus.IN_SERVICE,
            created_at=_at(18, 14, 5),
            updated_at=_at(21, 11, 0),
        ),
        Order(
            id="ORDER_003",
            user_id="user_001",
            plan_id="PLAN_DOC_DRAFT",
            service_name="法律文书起草",
            total_amount=Decimal("599.00"),
            payment_status=PaymentStatus.PAID,
            service_status=ServiceStatus.COMPLETED,
            created_at=_at(8, 9, 40),
            updated_at=_at(16, 16, 30),
        ),
        Order(
            id="ORDER_004",
            user_id="user_001",
            plan_id="PLAN_CONTRACT_REVIEW",
            service_name="合同审核服务",
            total_amount=Decimal("299.00"),
            payment_status=PaymentStatus.PAID,
            service_status=ServiceStatus.PENDING_START,
            created_at=_at(15, 20, 10),
            updated_at=_at(15, 20, 10),
        ),
        Order(
            id="ORDER_101",
            user_id="user_002",
            plan_id="PLAN_LEGAL_CONSULT",
            service_name="法律咨询服务",
            total_amount=Decimal("199.00"),
            payment_status=PaymentStatus.PAID,
            service_status=ServiceStatus.IN_SERVICE,
            created_at=_at(12, 10, 0),
            updated_at=_at(19, 15, 0),
        ),
    ]
    session.add_all(orders)
    await session.flush()

    session.add_all(
        [
            ServiceProgressEvent(
                order_id="ORDER_001", node="订单已受理", status="COMPLETED",
                occurred_at=_at(20, 10, 30), description="已收到订单，等待提交审核材料。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_001", node="材料待补充", status="CURRENT",
                occurred_at=_at(21, 9, 20), description="请在服务页面补充合同原件与对方主体信息。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_002", node="订单已受理", status="COMPLETED",
                occurred_at=_at(18, 14, 5), description="咨询订单已受理。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_002", node="咨询安排已确认", status="CURRENT",
                occurred_at=_at(21, 11, 0), description="咨询时间已确认，可在服务页面查看。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_003", node="订单已受理", status="COMPLETED",
                occurred_at=_at(8, 9, 40), description="文书需求已受理。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_003", node="文书已交付", status="COMPLETED",
                occurred_at=_at(16, 16, 30), description="初稿与修改稿已通过服务页面交付。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_101", node="订单已受理", status="COMPLETED",
                occurred_at=_at(12, 10, 0), description="咨询订单已受理。",
            ),
            ServiceProgressEvent(
                order_id="ORDER_101", node="咨询安排已确认", status="CURRENT",
                occurred_at=_at(19, 15, 0), description="咨询时间已确认。",
            ),
        ]
    )

    session.add_all(
        [
            Invoice(
                id="INV_001",
                order_id="ORDER_003",
                status=InvoiceStatus.ISSUED,
                invoice_type="ELECTRONIC",
                title_type="PERSONAL",
                title="张岚",
                amount=Decimal("599.00"),
                created_at=_at(17, 9, 10),
                updated_at=_at(18, 10, 0),
            ),
            Invoice(
                id="INV_002",
                order_id="ORDER_002",
                status=InvoiceStatus.SUBMITTED,
                invoice_type="ELECTRONIC",
                title_type="PERSONAL",
                title="张岚",
                amount=Decimal("199.00"),
                created_at=_at(20, 9, 0),
                updated_at=_at(20, 9, 0),
            ),
            AfterSale(
                id="AS_001",
                order_id="ORDER_101",
                user_id="user_002",
                kind=AfterSaleKind.REFUND,
                reason="咨询时间一直未确认，希望取消并退款。",
                requested_amount=Decimal("199.00"),
                application_status=AfterSaleApplicationStatus.REVIEWING,
                refund_status=RefundStatus.NOT_REFUNDED,
                created_at=_at(19, 16, 0),
                updated_at=_at(20, 10, 0),
            ),
        ]
    )

    await session.commit()
