from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from atguigu.app.dependencies import SessionDep, get_auth_service
from atguigu.app.services.admin import AdminService

router = APIRouter(prefix="/api/v1", tags=["AI Operations"])


@router.get("/admin/metrics")
async def get_admin_metrics(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """返回管理员观测面板使用的 AI Run 汇总指标。"""
    get_auth_service().get_authorized_user(authorization, "admin")
    return await AdminService(session).get_metrics()


@router.get("/admin/runtime")
async def get_runtime_status(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """返回当前推理部署状态；fixture 模式会明确标注。"""
    get_auth_service().get_authorized_user(authorization, "admin")
    status = request.app.state.model_status
    return {
        "runtime_mode": status.runtime_mode,
        "model_name": status.model_name,
        "model_artifact_version": status.artifact_version,
        "reachable": status.reachable,
        "detail": status.detail,
        "checked_at": status.checked_at,
    }


@router.get("/runs")
async def list_runs(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    state: str | None = Query(default=None, max_length=30),
    authorization: Annotated[str | None, Header()] = None,
) -> list[dict]:
    """返回最近的 AI Run；仅管理员可访问。"""
    get_auth_service().get_authorized_user(authorization, "admin")
    return await AdminService(session).list_runs(limit, state)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """返回一个 AI Run 的决策、计划、校验与脱敏工具调用。"""
    get_auth_service().get_authorized_user(authorization, "admin")
    return await AdminService(session).get_run(run_id)
