"""把模型决策校验为可执行计划。

模型不能扩大权限：节点、工具、缺失槽位和页面动作范围都由本模块重新计算。
"""

from atguigu.agent.harness.flows.catalog import FLOW_CATALOG
from atguigu.agent.harness.flows.contract import FlowSnapshot
from atguigu.agent.llm.policy import (
    ExecutionPlan,
    NextAction,
    PlanBudget,
    PolicyDecision,
    PolicyIntent,
)
from atguigu.agent.harness.skills.catalog import SKILL_CATALOG

HANDOFF_INTENTS = {
    PolicyIntent.COMPLAINT,
    PolicyIntent.HUMAN_TRANSFER,
    PolicyIntent.LEGAL_ADVICE_REQUEST,
}

INTENT_TO_FLOW = {
    PolicyIntent.PLAN_QUERY: "PLAN_QUERY",
    PolicyIntent.ORDER_QUERY: "ORDER_QUERY",
    PolicyIntent.INVOICE_QUERY: "INVOICE_QUERY",
    PolicyIntent.INVOICE_APPLY: "INVOICE_APPLY",
    PolicyIntent.SERVICE_PROGRESS_QUERY: "SERVICE_PROGRESS_QUERY",
    PolicyIntent.REFUND_APPLY: "REFUND_APPLY",
    PolicyIntent.AFTER_SALE_QUERY: "AFTER_SALE_QUERY",
    PolicyIntent.CANCEL_ORDER: "CANCEL_ORDER",
    PolicyIntent.POLICY_QUERY: "POLICY_QUERY",
}

CLARIFY_QUESTIONS = {
    "order_id": "请问你要办理的是哪一笔订单？可以告诉我订单号，也可以在下方订单卡片中选择。",
    "refund_reason": "方便说明一下申请退款的原因吗？例如买错套餐、服务尚未开始等。",
    "intent": "请问你想查询订单进度、了解服务套餐，还是申请退款或开票？",
    "plan_id": "请问你想了解哪一个服务套餐？可以告诉我套餐名称。",
}

# 查询类流程允许的通用页面入口
QUERY_PAGE_ACTIONS = {
    "ORDER_QUERY": ["OPEN_ORDER_DETAIL", "BROWSE_ORDERS"],
    "INVOICE_QUERY": ["OPEN_ORDER_DETAIL", "BROWSE_ORDERS"],
    "SERVICE_PROGRESS_QUERY": ["OPEN_ORDER_DETAIL", "BROWSE_ORDERS"],
    "AFTER_SALE_QUERY": ["VIEW_AFTER_SALES", "OPEN_ORDER_DETAIL"],
    "PLAN_QUERY": [],
    "POLICY_QUERY": [],
}

# 识别订单阶段就允许的补充查询工具（仍受 Skill 范围限制）
EXTRA_IDENTIFY_TOOLS = {
    "REFUND_APPLY": ("list_after_sales", "search_knowledge"),
    "INVOICE_APPLY": ("get_invoice",),
    "CANCEL_ORDER": (),
    "ORDER_QUERY": (),
    "SERVICE_PROGRESS_QUERY": ("get_service_progress",),
    "INVOICE_QUERY": ("get_invoice",),
    "AFTER_SALE_QUERY": ("list_after_sales",),
}


class ExecutionPlanner:
    """根据 PolicyDecision 与当前流程计算 ExecutionPlan。"""

    def build(self, decision: PolicyDecision, snapshot: FlowSnapshot) -> ExecutionPlan:
        # 1. 人工优先：投诉、明确转人工、专业法律问题不进入业务工具链路
        if decision.primary_intent in HANDOFF_INTENTS or decision.proposed_next_action == NextAction.HANDOFF:
            return self._handoff_plan(decision)

        flow_type = INTENT_TO_FLOW.get(decision.primary_intent)
        if flow_type is None:
            return self._unknown_plan(decision)

        flow = FLOW_CATALOG.require(flow_type)
        node = self._resolve_node(flow, snapshot, flow_type)
        node_def = flow.node(node)

        slots = self._merged_slots(snapshot, decision)
        action = decision.proposed_next_action
        clarify_slot: str | None = None
        reason = ""

        # 2. 页面动作需要满足目标节点的必填槽位
        if action == NextAction.OFFER_PAGE_ACTION:
            offer_node = "OFFER_APPLICATION" if flow.has_node("OFFER_APPLICATION") else node
            offer_def = flow.node(offer_node)
            missing = [name for name in offer_def.required_slots if not slots.get(name)]
            if missing:
                clarify_slot = missing[0]
                action = NextAction.ASK_CLARIFY
                node = self._clarify_node(flow, clarify_slot)
                reason = "页面动作前缺少必要槽位"
            elif offer_def.page_action is None and not QUERY_PAGE_ACTIONS.get(flow_type):
                action = NextAction.ANSWER
                reason = "当前节点不提供页面动作"
            else:
                node = offer_node
                node_def = flow.node(node)

        # 3. 澄清槽位由代码计算：优先流程定义顺序，其次节点必填项
        if action == NextAction.ASK_CLARIFY:
            clarify_slot = clarify_slot or self._compute_clarify_slot(flow, node_def, slots)
            if clarify_slot is None:
                # 没有可澄清项时给出安全回答，避免无意义追问
                action = NextAction.ANSWER if "ANSWER" in node_def.allowed_next_actions else NextAction.DECLINE
            else:
                node = self._clarify_node(flow, clarify_slot)
                node_def = flow.node(node)

        # 4. 识别订单阶段没有订单号：先查列表再决定是否澄清
        if (
            action in (NextAction.QUERY_BUSINESS, NextAction.QUERY_POLICY)
            and node == "IDENTIFY_ORDER"
            and not slots.get("order_id")
        ):
            reason = "先查询当前用户的订单列表，唯一匹配时不再追问"

        # 5. 动作必须落在节点允许集合内
        allowed_actions = list(node_def.allowed_next_actions)
        if action.value not in allowed_actions:
            action = self._fallback_action(allowed_actions, decision)
            if action == NextAction.ASK_CLARIFY:
                clarify_slot = clarify_slot or self._compute_clarify_slot(flow, node_def, slots)
                if clarify_slot is None:
                    # 没有可追问的关键槽位时不做无意义澄清
                    action = (
                        NextAction.ANSWER
                        if "ANSWER" in allowed_actions
                        else NextAction.DECLINE
                    )
            reason = f"模型动作越界，降级为 {action.value}"

        # 6. 工具白名单取节点与 Skill 的交集
        skill = SKILL_CATALOG.get_by_code(flow.skill)
        allowed_tools = [name for name in node_def.allowed_tools if name in skill.tools]
        # 识别订单后需要在同一轮完成该流程的核心查询，避免多一轮无意义往返
        if node == "IDENTIFY_ORDER":
            for extra in EXTRA_IDENTIFY_TOOLS.get(flow_type, ()):
                if extra in skill.tools and extra not in allowed_tools:
                    allowed_tools.append(extra)

        # 7. 页面动作白名单
        allowed_action_codes = list(QUERY_PAGE_ACTIONS.get(flow_type, []))
        if node_def.page_action:
            allowed_action_codes.append(node_def.page_action)
        # 查询后在同一轮内判断资格并给出申请入口，需要提前允许目标动作；
        # 最终是否可办理仍由 PageActionValidator 根据 allowed_actions 校验。
        if flow.has_node("OFFER_APPLICATION"):
            offer_action = flow.node("OFFER_APPLICATION").page_action
            if offer_action:
                allowed_action_codes.append(offer_action)
        if slots.get("order_id"):
            # 任何涉及具体订单的回答都可以引导到订单详情查看最新状态
            allowed_action_codes.append("OPEN_ORDER_DETAIL")
        if flow_type == "REFUND_APPLY":
            allowed_action_codes.extend(["APPLY_AFTER_SALE", "VIEW_AFTER_SALES"])
        if flow_type == "CANCEL_ORDER":
            allowed_action_codes.append("VIEW_AFTER_SALES")

        missing = [name for name in node_def.required_slots if not slots.get(name)]
        target = {
            key: str(slots[key])
            for key in ("order_id", "plan_id")
            if slots.get(key)
        }

        mode = "DIRECT" if action in (NextAction.ASK_CLARIFY, NextAction.HANDOFF, NextAction.DECLINE) else "EXECUTE"

        return ExecutionPlan(
            flow_type=flow_type,
            node=node,
            selected_skill=flow.skill,
            allowed_tool_names=sorted(set(allowed_tools)),
            required_slots=list(node_def.required_slots),
            missing_slots=missing,
            allowed_next_actions=allowed_actions,
            target_resource=target,
            mode=mode,
            planned_action=action.value,
            clarify_slot=clarify_slot,
            allowed_action_codes=sorted(set(allowed_action_codes)),
            budget=PlanBudget(),
            reason=reason,
        )

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _resolve_node(flow, snapshot: FlowSnapshot, flow_type: str) -> str:
        if snapshot.flow_type != flow_type:
            return flow.entry_node
        if snapshot.node in ("INIT", "HANDED_OFF", "NEEDS_REVALIDATION"):
            # 人工结束后必须重新核实事实，不沿用旧资格
            return flow.entry_node
        if flow.has_node(snapshot.node):
            return snapshot.node
        return flow.entry_node

    @staticmethod
    def _merged_slots(snapshot: FlowSnapshot, decision: PolicyDecision) -> dict[str, str]:
        slots: dict[str, str] = {}
        for name, slot in snapshot.slots.items():
            if slot.value:
                slots[name] = slot.value
        for name, update in decision.slot_updates.items():
            slots[name] = update.value
        return slots

    @staticmethod
    def _compute_clarify_slot(flow, node_def, slots: dict[str, str]) -> str | None:
        for name in flow.clarify_order:
            if not slots.get(name):
                return name
        for name in node_def.required_slots:
            if not slots.get(name):
                return name
        return None

    @staticmethod
    def _clarify_node(flow, slot_name: str) -> str:
        if slot_name == "order_id" and flow.has_node("IDENTIFY_ORDER"):
            return "IDENTIFY_ORDER"
        if slot_name == "refund_reason" and flow.has_node("COLLECT_REASON"):
            return "COLLECT_REASON"
        return flow.entry_node

    @staticmethod
    def _fallback_action(allowed_actions: list[str], decision: PolicyDecision) -> NextAction:
        for candidate in (
            NextAction.ASK_CLARIFY,
            NextAction.ANSWER,
            NextAction.HANDOFF,
            NextAction.DECLINE,
        ):
            if candidate.value in allowed_actions:
                return candidate
        return NextAction.DECLINE

    @staticmethod
    def _handoff_plan(decision: PolicyDecision) -> ExecutionPlan:
        skill = "policy-service"
        if decision.primary_intent == PolicyIntent.LEGAL_ADVICE_REQUEST:
            skill = "policy-service"
        return ExecutionPlan(
            flow_type=decision.primary_intent.value,
            node="HANDED_OFF",
            selected_skill=skill,
            allowed_tool_names=[],
            required_slots=[],
            missing_slots=[],
            allowed_next_actions=["HANDOFF"],
            mode="DIRECT",
            planned_action=NextAction.HANDOFF.value,
            allowed_action_codes=[],
            reason="投诉、明确转人工或专业法律问题不进入业务办理",
        )

    @staticmethod
    def _unknown_plan(decision: PolicyDecision) -> ExecutionPlan:
        action = (
            NextAction.ANSWER
            if decision.proposed_next_action == NextAction.ANSWER and decision.answer_note
            else NextAction.ASK_CLARIFY
        )
        return ExecutionPlan(
            flow_type="UNKNOWN",
            node="INIT",
            selected_skill="policy-service",
            allowed_tool_names=[],
            required_slots=[],
            missing_slots=[],
            allowed_next_actions=["ANSWER", "ASK_CLARIFY", "HANDOFF", "DECLINE"],
            mode="DIRECT",
            planned_action=action.value,
            clarify_slot="intent" if action == NextAction.ASK_CLARIFY else None,
            allowed_action_codes=[],
            reason="意图无法确定，先做一次意图澄清" if action == NextAction.ASK_CLARIFY else "简单说明",
        )
