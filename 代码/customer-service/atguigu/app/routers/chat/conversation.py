from typing import Annotated

from fastapi import APIRouter, Header

from atguigu.app.dependencies import ConversationServiceDep, get_auth_service
from atguigu.app.schemas.chat.conversation import (
    ConversationDetailResponse,
    CurrentConversationResponse,
)

router = APIRouter(prefix="/api/v1", tags=["聊天会话路由"])


@router.post("/conversations/current", response_model=CurrentConversationResponse)
async def get_current_conversation(
    conversation_service: ConversationServiceDep,
    authorization: Annotated[str | None, Header()] = None,
):
    """返回当前客户的有效会话与人工状态。

    权限：仅 customer，且只访问自己的会话。
    """
    authorized_user = get_auth_service().get_authorized_user(authorization, "customer")
    return await conversation_service.get_current_conversation(authorized_user.user_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_detail(
    conversation_id: str,
    conversation_service: ConversationServiceDep,
    authorization: Annotated[str | None, Header()] = None,
):
    """返回客服工作台所需的会话详情、交接上下文与流程摘要。

    权限：admin 全部；agent 仅限存在工单且等待接入或本人负责的会话。
    """
    current_user = get_auth_service().get_authorized_user(authorization, "agent", "admin")
    return await conversation_service.get_conversation_detail(
        conversation_id,
        requester_id=current_user.user_id,
        requester_role=current_user.role,
    )
