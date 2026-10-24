import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from atguigu.app.app import app
from atguigu.common.config import get_settings
from atguigu.infrastucture.db import session_factory
from atguigu.models.models import (
    Conversation,
    ConversationFlowState,
    Handoff,
    Message,
    RealtimeOutbox,
)

settings = get_settings()


def token_for(user_id: str, role: str = "customer") -> str:
    return jwt.encode(
        {"user_id": user_id, "role": role},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


def auth(user_id="user_001", role="customer"):
    return {"Authorization": f"Bearer {token_for(user_id, role)}"}


@pytest.mark.asyncio
async def test_same_message_id_with_same_content_is_idempotent(client):
    payload = {"message_id": "msg_fixed_001", "type": "text", "content": {"text": "我想退款"}}
    first = await client.post("/api/v1/chat/messages", json=payload, headers=auth())
    second = await client.post("/api/v1/chat/messages", json=payload, headers=auth())
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["conversation_id"] == second.json()["conversation_id"]

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Message))
    assert count == 1


@pytest.mark.asyncio
async def test_same_message_id_with_different_content_is_rejected(client):
    await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_fixed_002", "type": "text", "content": {"text": "我想退款"}},
        headers=auth(),
    )
    conflict = await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_fixed_002", "type": "text", "content": {"text": "我想开票"}},
        headers=auth(),
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_message_id_cannot_be_reused_by_another_user(client):
    await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_fixed_003", "type": "text", "content": {"text": "你好"}},
        headers=auth("user_001"),
    )
    conflict = await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_fixed_003", "type": "text", "content": {"text": "你好"}},
        headers=auth("user_002"),
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_content_validation_rejects_unknown_object_and_long_text(client):
    bad_object = await client.post(
        "/api/v1/chat/messages",
        json={
            "message_id": "msg_bad_001",
            "type": "object",
            "content": {"object_type": "product", "object_id": "PRODUCT_001"},
        },
        headers=auth(),
    )
    assert bad_object.status_code == 422

    long_text = await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_bad_002", "type": "text", "content": {"text": "退" * 2100}},
        headers=auth(),
    )
    assert long_text.status_code == 422


@pytest.mark.asyncio
async def test_explicit_human_request_creates_handoff_without_model(client):
    response = await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_human_001", "type": "text", "content": {"text": "转人工客服"}},
        headers=auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "QUEUED"
    assert body["handoff_created"] is True

    async with session_factory() as session:
        handoff = await session.scalar(select(Handoff))
        conversation = await session.scalar(select(Conversation))
        flow = await session.scalar(select(ConversationFlowState))
        events = await session.scalar(select(func.count()).select_from(RealtimeOutbox))

    assert handoff is not None
    assert handoff.reason_code == "USER_REQUESTED"
    assert handoff.queue_code == "platform_aftersales"
    assert conversation.mode == "QUEUED"
    # 没有在办流程时不强行创建流程状态
    assert flow is None
    assert events >= 2


@pytest.mark.asyncio
async def test_agent_cannot_read_conversation_without_handoff(client):
    await client.post(
        "/api/v1/chat/messages",
        json={"message_id": "msg_agent_001", "type": "text", "content": {"text": "我想退款"}},
        headers=auth(),
    )
    async with session_factory() as session:
        conversation = await session.scalar(select(Conversation))

    denied = await client.get(
        f"/api/v1/conversations/{conversation.id}", headers=auth("agent_001", "agent")
    )
    assert denied.status_code == 403

    allowed = await client.get(
        f"/api/v1/conversations/{conversation.id}", headers=auth("admin_001", "admin")
    )
    assert allowed.status_code == 200
    assert allowed.json()["conversation_id"] == conversation.id


@pytest.mark.asyncio
async def test_customer_cannot_access_admin_endpoints(client):
    response = await client.get("/api/v1/handoffs", headers=auth())
    assert response.status_code == 403
