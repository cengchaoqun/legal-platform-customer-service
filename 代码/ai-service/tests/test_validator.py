import pytest

from atguigu.agent.harness.tools.output import ToolFailureType
from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot
from atguigu.agent.harness.validator.answer import AnswerValidationError
from atguigu.agent.harness.validator.action import PageActionValidationError
from atguigu.agent.harness.validator.output import AgentOutputValidator
from atguigu.agent.llm.output import AgentOutput, PageActionRequest, ReplyType


class StubReader:
    def __init__(self, snapshots):
        self.snapshots = tuple(snapshots)

    async def read_by_run_id(self, run_id):
        return self.snapshots


def order_call(order_id="ORDER_001", allowed_actions=None, success=True):
    return ToolCallSnapshot(
        tool_name="get_order",
        arguments={"order_id": order_id},
        result={
            "success": True,
            "code": "ORDER_FOUND",
            "message": "ok",
            "data": {
                "id": order_id,
                "allowed_actions": allowed_actions or ["APPLY_AFTER_SALE", "APPLY_INVOICE"],
                "service_status": "WAITING_MATERIALS",
                "service_status_label": "材料待补充",
                "total_amount": "299.00",
            },
        },
        success=success,
    )


def validator(*snapshots):
    return AgentOutputValidator(StubReader(snapshots))


@pytest.mark.asyncio
async def test_commitment_claim_is_rejected():
    with pytest.raises(AnswerValidationError) as error:
        await validator(order_call()).validate(
            AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content="已为你提交退款申请，请等待到账。",
            ),
            "run_1",
        )
    assert "没有写权限" in str(error.value)


@pytest.mark.asyncio
async def test_unsupported_amount_is_rejected():
    with pytest.raises(AnswerValidationError):
        await validator(order_call()).validate(
            AgentOutput(reply_type=ReplyType.ANSWER, reply_content="可退金额为 888.00 元。"),
            "run_1",
        )


@pytest.mark.asyncio
async def test_supported_amount_and_status_pass():
    output = await validator(order_call()).validate(
        AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content="订单 ORDER_001 的金额是 299.00 元，服务状态为材料待补充。",
        ),
        "run_1",
    )
    assert output.reply_type == ReplyType.ANSWER


@pytest.mark.asyncio
async def test_conditional_status_is_not_treated_as_fact():
    # “审核通过后”是条件句，不应被当作已审核通过的事实
    await validator(order_call()).validate(
        AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content="提交后进入人工审核，审核通过后才会进入退款处理。",
        ),
        "run_1",
    )


@pytest.mark.asyncio
async def test_page_action_requires_order_evidence():
    with pytest.raises(PageActionValidationError):
        await validator().validate(
            AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content="可以在这里申请退款。",
                page_action_request=PageActionRequest(
                    action_code="APPLY_AFTER_SALE", resource_id="ORDER_002"
                ),
            ),
            "run_1",
            allowed_action_codes=["APPLY_AFTER_SALE"],
        )


@pytest.mark.asyncio
async def test_page_action_requires_business_permission():
    with pytest.raises(PageActionValidationError) as error:
        await validator(order_call(allowed_actions=["VIEW_SERVICE_PROGRESS"])).validate(
            AgentOutput(
                reply_type=ReplyType.ANSWER,
                reply_content="可以在这里申请退款。",
                page_action_request=PageActionRequest(
                    action_code="APPLY_AFTER_SALE", resource_id="ORDER_001"
                ),
            ),
            "run_1",
            allowed_action_codes=["APPLY_AFTER_SALE"],
        )
    assert "不允许" in str(error.value)


@pytest.mark.asyncio
async def test_page_action_succeeds_when_permitted():
    output = await validator(order_call()).validate(
        AgentOutput(
            reply_type=ReplyType.ANSWER,
            reply_content="订单 ORDER_001 可以提交退款申请。",
            page_action_request=PageActionRequest(
                action_code="APPLY_AFTER_SALE", resource_id="ORDER_001"
            ),
        ),
        "run_1",
        allowed_action_codes=["APPLY_AFTER_SALE"],
    )
    assert output.page_action.href == "/me/orders/ORDER_001/refund"


@pytest.mark.asyncio
async def test_single_question_clarification_rule():
    with pytest.raises(AnswerValidationError):
        await validator().validate(
            AgentOutput(
                reply_type=ReplyType.CLARIFY,
                reply_content="你想查哪一笔订单？是退款还是开票？",
                clarification_slot="order_id",
            ),
            "run_1",
            allowed_next_actions=["ASK_CLARIFY"],
        )


@pytest.mark.asyncio
async def test_clarification_option_must_come_from_tools():
    list_call = ToolCallSnapshot(
        tool_name="list_orders",
        arguments={},
        result={
            "success": True,
            "code": "ORDERS_FOUND",
            "message": "ok",
            "data": [{"id": "ORDER_001"}, {"id": "ORDER_002"}],
        },
        success=True,
    )
    from atguigu.agent.llm.output import ClarificationOption

    with pytest.raises(AnswerValidationError):
        await validator(list_call).validate(
            AgentOutput(
                reply_type=ReplyType.CLARIFY,
                reply_content="请选择订单？",
                clarification_slot="order_id",
                clarification_options=[
                    ClarificationOption(
                        object_type="order", object_id="ORDER_999", label="不存在的订单"
                    )
                ],
            ),
            "run_1",
            allowed_next_actions=["ASK_CLARIFY"],
        )


@pytest.mark.asyncio
async def test_business_failure_uses_business_message():
    failed = ToolCallSnapshot(
        tool_name="get_order",
        arguments={"order_id": "ORDER_777"},
        result={
            "success": False,
            "code": "NOT_FOUND",
            "message": "订单不存在",
            "failure_type": ToolFailureType.BUSINESS,
        },
        success=False,
        failure_type=ToolFailureType.BUSINESS,
    )
    output = await validator(failed).validate(
        AgentOutput(reply_type=ReplyType.ANSWER, reply_content="订单状态为服务中。"),
        "run_1",
    )
    assert output.reply_content == "订单不存在"
