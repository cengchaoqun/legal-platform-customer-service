from atguigu.agent.harness.errors import AgentOutputValidationError, CorrectableErrorCode
from atguigu.agent.harness.rules.fact import FactChecker, FactExtractor
from atguigu.agent.harness.tools.catalog import TOOL_CATALOG, ToolCategory
from atguigu.agent.harness.tools.output import ToolFailureType
from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot
from atguigu.agent.llm.output import AgentOutput, ReplyType


class AnswerValidationError(AgentOutputValidationError):
    """表示 Agent 回复不符合业务事实或流程规则。"""


class AnswerValidator:
    """校验 Agent 回复使用的业务数据与动作范围。"""

    business_failure_message = (
        "暂时无法取得可靠的订单、开票或售后信息，因此现在不能确认结果，"
        "请稍后重试，或点击“转人工客服”由人工核实。"
    )

    def __init__(self):
        self.fact_checker = FactChecker()

    def validate(
        self,
        output: AgentOutput,
        tool_calls: tuple[ToolCallSnapshot, ...],
        *,
        allowed_next_actions: list[str] | None = None,
        clarify_slot: str | None = None,
        require_knowledge: bool = False,
    ) -> AgentOutput:
        # 1. 回复类型必须在当前节点允许的动作范围内
        self._validate_reply_type(output, allowed_next_actions)

        # 2. 澄清必须只问一个具体问题，且追问槽位不能偏离服务端计划
        if output.reply_type == ReplyType.CLARIFY:
            self._validate_clarification(output)
            if clarify_slot and output.clarification_slot and output.clarification_slot != clarify_slot:
                raise AnswerValidationError(
                    CorrectableErrorCode.MODEL_OUTPUT_INVALID,
                    f"澄清槽位应为 {clarify_slot}，而不是 {output.clarification_slot}",
                )
            if output.clarification_slot and output.clarification_slot not in {
                "order_id",
                "plan_id",
                "refund_reason",
                "invoice_type",
                "intent",
            }:
                raise AnswerValidationError(
                    CorrectableErrorCode.MODEL_OUTPUT_INVALID,
                    f"不允许追问未知槽位：{output.clarification_slot}",
                )

        # 3. 业务工具全部失败时使用安全回复，不输出未经确认的事实
        business_calls = self._get_business_calls(tool_calls)
        successful_business_calls = self._get_successful_calls(business_calls)

        # 4. 澄清候选必须来自本次成功查询结果
        if output.clarification_options:
            self._validate_options(output.clarification_options, successful_business_calls)

        if business_calls and not successful_business_calls:
            return self._build_failure_reply(output, business_calls)

        # 4. 禁止声称已执行写操作（AI 没有任何写工具）
        claims = FactExtractor.find_commitment_claims(output.reply_content)
        if claims:
            raise AnswerValidationError(
                CorrectableErrorCode.UNSUPPORTED_FACT,
                "回复声称已完成业务操作，但智能客服没有写权限："
                + "、".join(claims),
            )

        # 5. 业务事实必须能在本次 Run 的成功工具结果中找到依据
        self._validate_facts(
            output.reply_content,
            successful_business_calls,
            include_status=not require_knowledge,
        )

        # 6. 政策类回答必须有政策检索依据
        if require_knowledge and output.reply_type != ReplyType.REQUEST_HANDOFF:
            has_knowledge = any(
                tool_call.tool_name == "search_knowledge"
                and tool_call.success is True
                and (tool_call.result or {}).get("data")
                for tool_call in tool_calls
            )
            if not has_knowledge:
                raise AnswerValidationError(
                    CorrectableErrorCode.UNSUPPORTED_FACT,
                    "回答平台规则前必须成功检索到政策条目；没有依据时说明无法确认并建议转人工",
                )
        return output

    @staticmethod
    def _validate_reply_type(
        output: AgentOutput, allowed_next_actions: list[str] | None
    ) -> None:
        if not allowed_next_actions:
            return
        mapping = {
            ReplyType.ANSWER: {"ANSWER", "OFFER_PAGE_ACTION", "QUERY_BUSINESS", "QUERY_POLICY"},
            ReplyType.CLARIFY: {"ASK_CLARIFY"},
            ReplyType.DECLINE: {"DECLINE"},
            ReplyType.REQUEST_HANDOFF: {"HANDOFF"},
        }
        allowed = mapping[output.reply_type]
        if not (allowed & set(allowed_next_actions)):
            raise AnswerValidationError(
                CorrectableErrorCode.UNVERIFIED_ACTION_RESOURCE,
                f"当前流程节点不允许 {output.reply_type.value}，请按计划执行",
            )

    @staticmethod
    def _validate_clarification(output: AgentOutput) -> None:
        question_marks = output.reply_content.count("？") + output.reply_content.count("?")
        if question_marks > 1:
            raise AnswerValidationError(
                CorrectableErrorCode.MODEL_OUTPUT_INVALID,
                "澄清一次只能询问一个关键问题",
            )

    @staticmethod
    def _validate_options(options, successful_calls: tuple[ToolCallSnapshot, ...]) -> None:
        """候选订单必须出现在本 Run 的成功订单结果中。"""
        known_orders: set[str] = set()
        for tool_call in successful_calls:
            data = (tool_call.result or {}).get("data")
            if tool_call.tool_name == "get_order" and isinstance(data, dict):
                known_orders.add(str(data.get("id", "")).upper())
            if tool_call.tool_name == "list_orders" and isinstance(data, list):
                known_orders.update(
                    str(item.get("id", "")).upper()
                    for item in data
                    if isinstance(item, dict)
                )
        for option in options:
            if option.object_type == "order" and option.object_id.upper() not in known_orders:
                raise AnswerValidationError(
                    CorrectableErrorCode.UNVERIFIED_ACTION_RESOURCE,
                    f"澄清候选订单未经工具确认：{option.object_id}",
                )

    @staticmethod
    def _get_business_calls(tool_calls: tuple[ToolCallSnapshot, ...]) -> tuple[ToolCallSnapshot, ...]:
        return tuple(
            tool_call
            for tool_call in tool_calls
            if TOOL_CATALOG.get_tool_category(tool_call.tool_name) == ToolCategory.BUSINESS
        )

    @staticmethod
    def _get_successful_calls(business_calls: tuple[ToolCallSnapshot, ...]) -> tuple[ToolCallSnapshot, ...]:
        return tuple(tool_call for tool_call in business_calls if tool_call.success is True)

    def _build_failure_reply(
        self, output: AgentOutput, business_calls: tuple[ToolCallSnapshot, ...]
    ) -> AgentOutput:
        """业务拒绝使用业务原话；服务/契约失败使用统一安全回复。"""
        business_failures = tuple(
            tool_call
            for tool_call in business_calls
            if tool_call.failure_type == ToolFailureType.BUSINESS
        )

        if len(business_failures) == len(business_calls):
            latest = business_failures[-1]
            message = str((latest.result or {}).get("message") or "业务系统未返回结果")
            return output.model_copy(
                update={
                    "reply_type": ReplyType.ANSWER,
                    "reply_content": message,
                    "handoff_request": None,
                    "page_action_request": None,
                }
            )

        return output.model_copy(
            update={
                "reply_type": ReplyType.DECLINE,
                "reply_content": self.business_failure_message,
                "handoff_request": None,
                "page_action_request": None,
            }
        )

    def _validate_facts(
        self,
        reply_content: str,
        successful_calls: tuple[ToolCallSnapshot, ...],
        *,
        include_status: bool = True,
    ) -> None:
        unsupported = self.fact_checker.get_unsupported_facts(
            reply_content, successful_calls, include_status=include_status
        )
        if unsupported:
            values = "、".join(fact.value for fact in unsupported)
            raise AnswerValidationError(
                CorrectableErrorCode.UNSUPPORTED_FACT,
                f"回复中的业务事实缺少工具支持：{values}",
            )
