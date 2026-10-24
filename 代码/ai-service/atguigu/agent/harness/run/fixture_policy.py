"""确定性的离线决策器。

用途：在不调用线上 API 的环境中跑通协议、流程、工具与并发测试。
它不是上线模型，也不能用来宣称模型能力：运行审计会记录 runtime_mode=FIXTURE，
模型版本记为 fixture-policy-v1，健康检查接口同样会暴露该状态。
"""

import re
from datetime import datetime

from atguigu.agent.harness.flows.contract import FlowSnapshot
from atguigu.agent.llm.policy import (
    ClarificationRequest,
    ConflictSignal,
    HandoffReasonCode,
    NextAction,
    PolicyDecision,
    PolicyIntent,
    SlotUpdate,
)
from atguigu.app.schemas.run import AgentRunRequest

ORDER_ID_PATTERN = re.compile(r"\bORDER[_\-]?(\d{1,6})\b", re.IGNORECASE)
PLAN_ID_PATTERN = re.compile(r"\bPLAN[_\-][A-Z_]{2,40}\b", re.IGNORECASE)

PLAN_NAME_MAP = {
    "合同审核": "PLAN_CONTRACT_REVIEW",
    "法律咨询": "PLAN_LEGAL_CONSULT",
    "文书": "PLAN_DOC_DRAFT",
    "常法": "PLAN_ANNUAL_ADVISOR",
    "顾问": "PLAN_ANNUAL_ADVISOR",
}

PROGRESS_WORDS = ("进度", "到哪", "到账", "审核到", "审核结果", "处理到", "怎么样了", "没更新", "结果")

PRIORITY = (
    PolicyIntent.HUMAN_TRANSFER,
    PolicyIntent.COMPLAINT,
    PolicyIntent.LEGAL_ADVICE_REQUEST,
    PolicyIntent.REFUND_APPLY,
    PolicyIntent.INVOICE_APPLY,
    PolicyIntent.CANCEL_ORDER,
    PolicyIntent.AFTER_SALE_QUERY,
    PolicyIntent.INVOICE_QUERY,
    PolicyIntent.SERVICE_PROGRESS_QUERY,
    PolicyIntent.ORDER_QUERY,
    PolicyIntent.PLAN_QUERY,
    PolicyIntent.POLICY_QUERY,
)

INTENT_TO_FLOW_TYPES = {
    "REFUND_APPLY",
    "INVOICE_APPLY",
    "CANCEL_ORDER",
    "ORDER_QUERY",
    "SERVICE_PROGRESS_QUERY",
    "INVOICE_QUERY",
    "AFTER_SALE_QUERY",
    "PLAN_QUERY",
    "POLICY_QUERY",
}

GREETINGS = ("你好", "您好", "在吗", "hello", "hi")


class FixturePolicyPredictor:
    """规则驱动的测试替身决策器。"""

    async def predict(self, request: AgentRunRequest) -> PolicyDecision:
        text, object_slots, object_only = self._collect(request)
        intents = self._detect_intents(text)

        # 只发送订单卡片通常是在回答上一轮的澄清，沿用当前流程意图
        if (
            object_only
            and request.flow_state
            and request.flow_state.flow_type
            and request.flow_state.flow_type in INTENT_TO_FLOW_TYPES
        ):
            flow_intent = PolicyIntent(request.flow_state.flow_type)
            intents = [flow_intent, *[item for item in intents if item != flow_intent]]

        primary = self._primary(intents)

        # 短回答补槽：当前节点在收集退款原因时，用户的一句话就是原因本身
        if (
            not intents
            and request.flow_state
            and request.flow_state.node == "COLLECT_REASON"
            and request.flow_state.flow_type
        ):
            flow_intent = PolicyIntent(request.flow_state.flow_type)
            intents = [flow_intent]
            primary = flow_intent

        slot_updates: dict[str, SlotUpdate] = {}
        conflicts: list[ConflictSignal] = []

        # 1. 对象消息直接视为明确的业务对象
        if object_slots.get("order_id"):
            slot_updates["order_id"] = SlotUpdate(
                value=object_slots["order_id"],
                source_message_id=request.messages[-1].message_id,
            )
        if object_slots.get("plan_id"):
            slot_updates["plan_id"] = SlotUpdate(
                value=object_slots["plan_id"],
                source_message_id=request.messages[-1].message_id,
            )

        # 2. 文本中的订单号；出现多个不同编号时标记冲突
        order_ids = self._extract_order_ids(text)
        if len(order_ids) > 1:
            conflicts.append(
                ConflictSignal(
                    slot_name="order_id",
                    existing_value=order_ids[0],
                    new_value=order_ids[1],
                    reason="同一句中出现多个订单编号，需要用户确认",
                )
            )
        elif order_ids and "order_id" not in slot_updates:
            # 已在流程中验证过的同一订单不重复写入
            exists = (
                request.flow_state
                and request.flow_state.slots.get("order_id")
                and request.flow_state.slots["order_id"].value == order_ids[0]
            )
            if not exists:
                slot_updates["order_id"] = SlotUpdate(
                    value=order_ids[0], source_message_id=request.messages[-1].message_id
                )

        # 3. 退款原因
        if primary == PolicyIntent.REFUND_APPLY:
            reason = self._extract_refund_reason(text)
            existing_reason = (
                request.flow_state.slots.get("refund_reason").value
                if request.flow_state and request.flow_state.slots.get("refund_reason")
                else None
            )
            if reason and reason != existing_reason:
                slot_updates["refund_reason"] = SlotUpdate(
                    value=reason, source_message_id=request.messages[-1].message_id
                )

        # 4. 套餐线索
        for name, plan_id in PLAN_NAME_MAP.items():
            if name in text and "plan_id" not in slot_updates:
                slot_updates["plan_id"] = SlotUpdate(
                    value=plan_id, source_message_id=request.messages[-1].message_id
                )
                break

        action, clarification, handoff_reason, handoff_summary, answer_note = self._decide(
            primary, intents, request.flow_state, slot_updates, text
        )

        return PolicyDecision(
            intents=intents or [PolicyIntent.UNKNOWN],
            primary_intent=primary,
            slot_updates=slot_updates,
            conflicts=conflicts,
            proposed_next_action=action,
            clarification=clarification,
            handoff_reason=handoff_reason,
            handoff_summary=handoff_summary,
            answer_note=answer_note,
        )

    # ------------------------------------------------------------------ 解析
    @staticmethod
    def _collect(request: AgentRunRequest) -> tuple[str, dict[str, str], bool]:
        """返回 (最新一条用户文本, 对象槽位, 是否纯对象消息)。

        注意：意图与槽位检测只针对最新一条用户消息。历史消息只用于上下文，
        否则历史里出现过的“转人工”等关键词会污染后续每一轮的意图判定。
        """
        latest_text = ""
        object_slots: dict[str, str] = {}
        object_only = True
        for message in request.messages:
            if message.type == "text":
                object_only = False
                latest_text = str(message.content.get("text", ""))
            else:
                object_type = str(message.content.get("object_type", ""))
                object_id = str(message.content.get("object_id", ""))
                if object_type == "order" and object_id:
                    object_slots["order_id"] = object_id.upper()
                if object_type == "plan" and object_id:
                    object_slots["plan_id"] = object_id.upper()
                query_text = message.content.get("query_text")
                if query_text:
                    latest_text = str(query_text)
                    object_only = False
        return latest_text, object_slots, object_only

    @staticmethod
    def _detect_intents(text: str) -> list[PolicyIntent]:
        intents: list[PolicyIntent] = []
        has_progress = any(word in text for word in PROGRESS_WORDS)

        if any(word in text for word in ("转人工", "人工客服", "找人工", "真人客服")):
            intents.append(PolicyIntent.HUMAN_TRANSFER)
        if any(word in text for word in ("投诉", "举报", "太差", "不满意", "态度")):
            intents.append(PolicyIntent.COMPLAINT)
        if any(
            word in text
            for word in (
                "律师", "起诉", "打官司", "胜诉", "能不能赢", "能赢", "赢吗",
                "法律意见", "案情", "赔偿标准", "怎么维权",
            )
        ):
            intents.append(PolicyIntent.LEGAL_ADVICE_REQUEST)

        # 政策咨询与办理申请需要区分：“能不能退/怎么退”属于规则咨询
        policy_words = ("政策", "规则", "条件", "怎么退", "能不能退", "怎么开", "时限", "要求是什么")
        # 注意：“开票”一词歧义大（“开票的时限”是咨询，“帮我开票”才是申请），
        # 动作意愿必须依赖“我要/我想/申请/办理/帮我”等明确意愿词，不能只看业务词。
        action_words = ("我要", "我想", "申请", "帮我", "办理", "退掉", "取消")
        asks_policy = any(word in text for word in policy_words) and not any(
            word in text for word in action_words
        )

        refund_word = any(word in text for word in ("退款", "退钱", "退费"))
        if refund_word and not asks_policy:
            intents.append(
                PolicyIntent.AFTER_SALE_QUERY if has_progress else PolicyIntent.REFUND_APPLY
            )
        if any(word in text for word in ("开票", "发票")) and not asks_policy:
            query_hint = has_progress or any(word in text for word in ("开好", "失败", "状态"))
            intents.append(
                PolicyIntent.INVOICE_QUERY if query_hint else PolicyIntent.INVOICE_APPLY
            )
        if "取消" in text and not refund_word:
            intents.append(PolicyIntent.CANCEL_ORDER)
        if not refund_word and any(word in text for word in ("进度", "到哪一步", "材料", "办理到")):
            intents.append(PolicyIntent.SERVICE_PROGRESS_QUERY)
        if any(word in text for word in ("套餐", "服务")) and any(
            word in text for word in ("价格", "多少钱", "权益", "包含什么", "介绍", "区别", "有什么套餐")
        ):
            intents.append(PolicyIntent.PLAN_QUERY)
        if asks_policy:
            intents.append(PolicyIntent.POLICY_QUERY)
        if "订单" in text and not intents:
            intents.append(PolicyIntent.ORDER_QUERY)

        return list(dict.fromkeys(intents))

    @staticmethod
    def _primary(intents: list[PolicyIntent]) -> PolicyIntent:
        for candidate in PRIORITY:
            if candidate in intents:
                return candidate
        return PolicyIntent.UNKNOWN

    @staticmethod
    def _extract_order_ids(text: str) -> list[str]:
        values = []
        for digits in ORDER_ID_PATTERN.findall(text):
            values.append(f"ORDER_{digits.zfill(3)}")
        return list(dict.fromkeys(values))

    @staticmethod
    def _extract_refund_reason(text: str) -> str | None:
        if "买错" in text:
            return "买错套餐"
        if "不需要" in text or "不想" in text:
            return "不再需要该服务"
        if any(word in text for word in ("没开始", "未开始", "还没开始")):
            return "服务尚未开始"
        if any(word in text for word in ("等太久", "太慢", "时间太长")):
            return "等待时间过长"
        match = re.search(r"(?:因为|原因是|由于)(.{2,30})", text)
        if match:
            return match.group(1).strip("。！! ")
        return None

    # ------------------------------------------------------------------ 动作
    @staticmethod
    def _decide(
        primary: PolicyIntent,
        intents: list[PolicyIntent],
        flow_state: FlowSnapshot | None,
        slot_updates: dict[str, SlotUpdate],
        text: str,
    ):
        normalized = text.strip().lower().strip("!！?？。,. ")
        if normalized in GREETINGS:
            return (
                NextAction.ANSWER,
                None,
                None,
                None,
                "你好，我是法律咨询平台的智能客服。可以帮你查询套餐、订单进度、开票和退款售后，也可以转人工。",
            )
        if primary == PolicyIntent.HUMAN_TRANSFER:
            return (
                NextAction.HANDOFF,
                None,
                HandoffReasonCode.USER_REQUESTED,
                "用户明确要求人工客服",
                None,
            )
        if primary == PolicyIntent.COMPLAINT:
            return (
                NextAction.HANDOFF,
                None,
                HandoffReasonCode.COMPLAINT,
                "用户对服务或处理结果表达投诉，需要人工跟进",
                None,
            )
        if primary == PolicyIntent.LEGAL_ADVICE_REQUEST:
            return (
                NextAction.HANDOFF,
                None,
                HandoffReasonCode.LEGAL_ADVICE,
                "用户提出具体案情或法律意见需求，需转专业渠道",
                None,
            )
        if primary == PolicyIntent.UNKNOWN:
            return (
                NextAction.ASK_CLARIFY,
                ClarificationRequest(
                    slot_name="intent",
                    question="请问你想查询订单进度、了解服务套餐，还是申请退款或开票？",
                ),
                None,
                None,
                None,
            )
        if primary == PolicyIntent.POLICY_QUERY:
            return NextAction.QUERY_POLICY, None, None, None, None

        if primary in (
            PolicyIntent.REFUND_APPLY,
            PolicyIntent.INVOICE_APPLY,
            PolicyIntent.CANCEL_ORDER,
            PolicyIntent.ORDER_QUERY,
            PolicyIntent.SERVICE_PROGRESS_QUERY,
            PolicyIntent.INVOICE_QUERY,
            PolicyIntent.AFTER_SALE_QUERY,
            PolicyIntent.PLAN_QUERY,
        ):
            node = (
                flow_state.node
                if flow_state and flow_state.flow_type == primary.value
                else None
            )
            existing_reason = (
                flow_state.slots.get("refund_reason").value
                if flow_state and flow_state.slots.get("refund_reason")
                else None
            )
            if primary == PolicyIntent.REFUND_APPLY and node == "COLLECT_REASON":
                if slot_updates.get("refund_reason") or existing_reason:
                    return NextAction.OFFER_PAGE_ACTION, None, None, None, None
                return (
                    NextAction.ASK_CLARIFY,
                    ClarificationRequest(
                        slot_name="refund_reason",
                        question="方便说明一下申请退款的原因吗？",
                    ),
                    None,
                    None,
                    None,
                )
            return NextAction.QUERY_BUSINESS, None, None, None, None

        return (
            NextAction.ASK_CLARIFY,
            ClarificationRequest(slot_name="intent", question="请问你具体想咨询哪一项服务？"),
            None,
            None,
            None,
        )
