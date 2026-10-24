from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from atguigu_legal.auth import AuthService
from atguigu_legal.config import get_settings
from atguigu_legal.db import get_session, session_factory
from atguigu_legal.models import Base, DemoUser
from atguigu_legal.schemas import (
    AfterSaleApplicationRequest,
    CancelOrderRequest,
    DemoTokenRequest,
    DemoTokenResponse,
    InvoiceApplicationRequest,
    Principal,
    ToolEnvelope,
)
from atguigu_legal.seed import seed_demo_data
from atguigu_legal.service import LegalBusinessService

router = APIRouter(prefix="/api/v1")
auth_service = AuthService()


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _envelope(code: str, message: str, data) -> dict:
    return ToolEnvelope(success=True, code=code, message=message, data=data).model_dump(
        mode="json"
    )


async def _customer_id(authorization: str | None) -> str:
    return str(auth_service.get_principal(authorization, "customer")["user_id"])


# --------------------------------------------------------------------- 身份
@router.post("/auth/demo-token", response_model=DemoTokenResponse)
async def issue_demo_token(payload: DemoTokenRequest, session: SessionDep):
    """签发演示账号令牌。

    生产环境由法律平台身份体系签发，本接口仅用于本地联调与测试。
    """
    user = await session.get(DemoUser, payload.user_id)
    if user is None:
        raise StarletteHTTPException(status_code=404, detail="演示账号不存在")
    if user.role != payload.role:
        raise StarletteHTTPException(status_code=409, detail="账号角色与请求不一致")
    token, expires_in = auth_service.issue_demo_token(user.id, user.role, user.display_name)
    principal = Principal(
        user_id=user.id,
        role=user.role,
        display_name=user.display_name,
        membership=user.membership if user.role == "customer" else None,
    )
    return DemoTokenResponse(
        access_token=token,
        expires_in=expires_in,
        principal=principal,
        user_id=user.id,
        role=user.role,
        display_name=user.display_name,
    )


@router.get("/demo/users")
async def list_demo_users(session: SessionDep):
    users = (await session.scalars(select(DemoUser).order_by(DemoUser.id))).all()
    return [
        {
            "id": user.id,
            "name": user.display_name,
            "role": user.role,
            "membership": user.membership,
        }
        for user in users
    ]


# --------------------------------------------------------------------- 套餐
@router.get("/plans")
async def list_plans(
    session: SessionDep,
    query: str | None = Query(default=None, max_length=80),
    service_type: str | None = Query(default=None, max_length=40),
):
    service = LegalBusinessService(session)
    plans = await service.list_plans(query, service_type)
    return _envelope(
        "PLANS_FOUND" if plans else "PLANS_EMPTY",
        "已返回套餐列表" if plans else "没有匹配的套餐",
        [plan.model_dump(mode="json") for plan in plans],
    )


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, session: SessionDep):
    service = LegalBusinessService(session)
    plan = await service.get_plan(plan_id)
    return _envelope("PLAN_FOUND", "已返回套餐详情", plan.model_dump(mode="json"))


# --------------------------------------------------------------------- 订单
@router.get("/orders")
async def list_orders(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
):
    user_id = await _customer_id(authorization)
    orders = await LegalBusinessService(session).list_orders(user_id)
    return _envelope(
        "ORDERS_FOUND" if orders else "ORDERS_EMPTY",
        "已返回订单列表" if orders else "当前账号没有订单",
        [order.model_dump(mode="json") for order in orders],
    )


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await _customer_id(authorization)
    order = await LegalBusinessService(session).get_order(user_id, order_id)
    return _envelope("ORDER_FOUND", "已返回订单详情", order.model_dump(mode="json"))


@router.get("/orders/{order_id}/service-progress")
async def get_service_progress(
    order_id: str,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await _customer_id(authorization)
    progress = await LegalBusinessService(session).get_service_progress(user_id, order_id)
    return _envelope("SERVICE_PROGRESS_FOUND", "已返回办理进度", progress.model_dump(mode="json"))


@router.get("/orders/{order_id}/invoice")
async def get_invoice(
    order_id: str,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await _customer_id(authorization)
    invoice = await LegalBusinessService(session).get_invoice(user_id, order_id)
    return _envelope("INVOICE_FOUND", invoice.status_label, invoice.model_dump(mode="json"))


@router.get("/after-sales")
async def list_after_sales(
    session: SessionDep,
    order_id: str | None = Query(default=None),
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await _customer_id(authorization)
    records = await LegalBusinessService(session).list_after_sales(user_id, order_id)
    return _envelope(
        "AFTER_SALES_FOUND" if records else "AFTER_SALES_EMPTY",
        "已返回售后记录" if records else "没有售后记录",
        [record.model_dump(mode="json") for record in records],
    )


# ----------------------------------------------------------------- 受控写入
@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    session: SessionDep,
    payload: CancelOrderRequest | None = None,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    user_id = await _customer_id(authorization)
    result = await LegalBusinessService(session).cancel_order(
        user_id,
        order_id,
        idempotency_key=idempotency_key or "",
        reason=payload.reason if payload else None,
    )
    return _envelope("ORDER_CANCELLED", "订单已取消", result)


@router.post("/orders/{order_id}/invoice-applications")
async def apply_invoice(
    order_id: str,
    payload: InvoiceApplicationRequest,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    user_id = await _customer_id(authorization)
    result = await LegalBusinessService(session).apply_invoice(
        user_id,
        order_id,
        idempotency_key=idempotency_key or "",
        invoice_type=payload.invoice_type,
        title_type=payload.title_type,
        title=payload.title,
        tax_id=payload.tax_id,
        email=payload.email,
    )
    return _envelope("INVOICE_APPLICATION_SUBMITTED", "开票申请已受理", result)


@router.post("/orders/{order_id}/after-sales")
async def apply_after_sale(
    order_id: str,
    payload: AfterSaleApplicationRequest,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    user_id = await _customer_id(authorization)
    result = await LegalBusinessService(session).apply_after_sale(
        user_id,
        order_id,
        idempotency_key=idempotency_key or "",
        kind=payload.kind,
        reason=payload.reason,
        requested_amount=payload.requested_amount,
    )
    return _envelope("AFTER_SALE_APPLICATION_SUBMITTED", "售后申请已提交", result)


# --------------------------------------------------------------------- 管理
@router.get("/admin/summary")
async def admin_summary(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
):
    auth_service.get_principal(authorization, "admin")
    summary = await LegalBusinessService(session).admin_summary()
    return summary


# --------------------------------------------------------------------- 应用
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表并导入演示数据，便于本地一键运行。"""
    settings = get_settings()
    from atguigu_legal.db import db_engine

    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    if settings.seed_demo_data:
        async with session_factory() as session:
            await seed_demo_data(session)
    yield


app = FastAPI(title="Legal Demo Business Service", lifespan=lifespan)
app.include_router(router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """把业务拒绝转换成统一外壳，方便 AI 工具区分业务失败与服务失败。"""
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code", "BUSINESS_REJECTED"))
        message = str(detail.get("message", "请求未被受理"))
    else:
        code = "NOT_FOUND" if exc.status_code == 404 else "REQUEST_REJECTED"
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "code": code, "message": message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "code": "VALIDATION_ERROR",
            "message": "请求字段不符合要求",
            "data": {"errors": exc.errors()},
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "legal-business-service", "mode": "demo"}
