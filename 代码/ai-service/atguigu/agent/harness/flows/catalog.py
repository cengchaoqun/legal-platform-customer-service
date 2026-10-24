from atguigu.agent.harness.flows.definition import FlowDefinition, NodeDefinition

# 通用动作集合：查询类节点不提供页面操作
QUERY_ACTIONS = ("QUERY_BUSINESS", "QUERY_POLICY", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE")

_ORDER_IDENTIFY = NodeDefinition(
    node="IDENTIFY_ORDER",
    required_slots=("order_id",),
    allowed_tools=("list_orders", "get_order"),
    allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
    after_query="CHECK_ORDER",
)

_ANSWERED_ORDER = NodeDefinition(
    node="ANSWERED",
    required_slots=(),
    allowed_tools=("list_orders", "get_order"),
    allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE"),
    after_answer="ANSWERED",
)

REFUND_APPLY = FlowDefinition(
    flow_type="REFUND_APPLY",
    skill="aftersales-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id", "refund_reason"),
    nodes={
        "IDENTIFY_ORDER": _ORDER_IDENTIFY,
        "CHECK_ORDER": NodeDefinition(
            node="CHECK_ORDER",
            required_slots=("order_id",),
            allowed_tools=("get_order", "list_after_sales", "search_knowledge"),
            allowed_next_actions=(
                "QUERY_BUSINESS", "QUERY_POLICY", "ASK_CLARIFY", "ANSWER",
                "OFFER_PAGE_ACTION", "HANDOFF", "DECLINE",
            ),
            page_action="APPLY_AFTER_SALE",
            after_query="CHECK_ORDER",
            after_answer="ANSWERED",
        ),
        "COLLECT_REASON": NodeDefinition(
            node="COLLECT_REASON",
            required_slots=("order_id", "refund_reason"),
            allowed_tools=("get_order", "search_knowledge"),
            allowed_next_actions=("ASK_CLARIFY", "OFFER_PAGE_ACTION", "HANDOFF", "DECLINE"),
            page_action="APPLY_AFTER_SALE",
            after_answer="ANSWERED",
        ),
        "OFFER_APPLICATION": NodeDefinition(
            node="OFFER_APPLICATION",
            required_slots=("order_id", "refund_reason"),
            allowed_tools=("get_order", "list_after_sales"),
            allowed_next_actions=("OFFER_PAGE_ACTION", "ANSWER", "HANDOFF"),
            page_action="APPLY_AFTER_SALE",
        ),
        "WAIT_USER_SUBMISSION": NodeDefinition(
            node="WAIT_USER_SUBMISSION",
            required_slots=("order_id",),
            allowed_tools=("list_after_sales", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ANSWER", "OFFER_PAGE_ACTION"),
            page_action="APPLY_AFTER_SALE",
        ),
        "APPLICATION_SUBMITTED": NodeDefinition(
            node="APPLICATION_SUBMITTED",
            required_slots=("order_id",),
            allowed_tools=("list_after_sales", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ANSWER", "DECLINE"),
        ),
        "NEEDS_REVALIDATION": NodeDefinition(
            node="NEEDS_REVALIDATION",
            required_slots=("order_id",),
            allowed_tools=("get_order", "list_after_sales", "search_knowledge"),
            allowed_next_actions=("QUERY_BUSINESS", "QUERY_POLICY", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
            after_query="CHECK_ORDER",
        ),
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("get_order", "list_after_sales"),
            allowed_next_actions=("ANSWER", "HANDOFF", "DECLINE"),
        ),
    },
)

INVOICE_APPLY = FlowDefinition(
    flow_type="INVOICE_APPLY",
    skill="order-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id",),
    nodes={
        "IDENTIFY_ORDER": _ORDER_IDENTIFY,
        "CHECK_ELIGIBILITY": NodeDefinition(
            node="CHECK_ELIGIBILITY",
            required_slots=("order_id",),
            allowed_tools=("get_order", "get_invoice"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "OFFER_PAGE_ACTION", "HANDOFF", "DECLINE"),
            page_action="APPLY_INVOICE",
            after_query="CHECK_ELIGIBILITY",
        ),
        "CHECK_ORDER": NodeDefinition(
            node="CHECK_ORDER",
            required_slots=("order_id",),
            allowed_tools=("get_order", "get_invoice"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "OFFER_PAGE_ACTION", "HANDOFF", "DECLINE"),
            page_action="APPLY_INVOICE",
            after_query="CHECK_ELIGIBILITY",
        ),
        "OFFER_APPLICATION": NodeDefinition(
            node="OFFER_APPLICATION",
            required_slots=("order_id",),
            allowed_tools=("get_order", "get_invoice"),
            allowed_next_actions=("OFFER_PAGE_ACTION", "ANSWER", "HANDOFF"),
            page_action="APPLY_INVOICE",
        ),
        "WAIT_USER_SUBMISSION": NodeDefinition(
            node="WAIT_USER_SUBMISSION",
            required_slots=("order_id",),
            allowed_tools=("get_invoice", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ANSWER", "OFFER_PAGE_ACTION"),
            page_action="APPLY_INVOICE",
        ),
        "APPLICATION_SUBMITTED": NodeDefinition(
            node="APPLICATION_SUBMITTED",
            required_slots=("order_id",),
            allowed_tools=("get_invoice", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ANSWER", "DECLINE"),
        ),
        "NEEDS_REVALIDATION": NodeDefinition(
            node="NEEDS_REVALIDATION",
            required_slots=("order_id",),
            allowed_tools=("get_order", "get_invoice"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
            after_query="CHECK_ELIGIBILITY",
        ),
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("get_order", "get_invoice"),
            allowed_next_actions=("ANSWER", "HANDOFF", "DECLINE"),
        ),
    },
)

CANCEL_ORDER = FlowDefinition(
    flow_type="CANCEL_ORDER",
    skill="order-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id",),
    nodes={
        "IDENTIFY_ORDER": _ORDER_IDENTIFY,
        "CHECK_ELIGIBILITY": NodeDefinition(
            node="CHECK_ELIGIBILITY",
            required_slots=("order_id",),
            allowed_tools=("get_order",),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "OFFER_PAGE_ACTION", "HANDOFF", "DECLINE"),
            page_action="CANCEL_ORDER",
            after_query="CHECK_ELIGIBILITY",
        ),
        "CHECK_ORDER": NodeDefinition(
            node="CHECK_ORDER",
            required_slots=("order_id",),
            allowed_tools=("get_order",),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "OFFER_PAGE_ACTION", "HANDOFF", "DECLINE"),
            page_action="CANCEL_ORDER",
            after_query="CHECK_ELIGIBILITY",
        ),
        "OFFER_APPLICATION": NodeDefinition(
            node="OFFER_APPLICATION",
            required_slots=("order_id",),
            allowed_tools=("get_order",),
            allowed_next_actions=("OFFER_PAGE_ACTION", "ANSWER", "HANDOFF"),
            page_action="CANCEL_ORDER",
        ),
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("get_order", "list_orders"),
            allowed_next_actions=("ANSWER", "HANDOFF", "DECLINE"),
        ),
        "NEEDS_REVALIDATION": NodeDefinition(
            node="NEEDS_REVALIDATION",
            required_slots=("order_id",),
            allowed_tools=("get_order",),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
            after_query="CHECK_ELIGIBILITY",
        ),
    },
)

ORDER_QUERY = FlowDefinition(
    flow_type="ORDER_QUERY",
    skill="order-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id",),
    nodes={
        "IDENTIFY_ORDER": _ORDER_IDENTIFY,
        "ANSWERED": _ANSWERED_ORDER,
        "NEEDS_REVALIDATION": _ORDER_IDENTIFY,
    },
)

SERVICE_PROGRESS_QUERY = FlowDefinition(
    flow_type="SERVICE_PROGRESS_QUERY",
    skill="progress-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id",),
    nodes={
        "IDENTIFY_ORDER": _ORDER_IDENTIFY,
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("list_orders", "get_order", "get_service_progress"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE"),
            after_answer="ANSWERED",
        ),
        "NEEDS_REVALIDATION": _ORDER_IDENTIFY,
    },
)

AFTER_SALE_QUERY = FlowDefinition(
    flow_type="AFTER_SALE_QUERY",
    skill="aftersales-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id",),
    nodes={
        "IDENTIFY_ORDER": NodeDefinition(
            node="IDENTIFY_ORDER",
            required_slots=(),
            allowed_tools=("list_after_sales", "list_orders", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE"),
            after_query="ANSWERED",
        ),
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("list_after_sales", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ANSWER", "HANDOFF", "DECLINE"),
            after_answer="ANSWERED",
        ),
        "NEEDS_REVALIDATION": NodeDefinition(
            node="NEEDS_REVALIDATION",
            required_slots=(),
            allowed_tools=("list_after_sales", "get_order"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
            after_query="ANSWERED",
        ),
    },
)

INVOICE_QUERY = FlowDefinition(
    flow_type="INVOICE_QUERY",
    skill="order-service",
    entry_node="IDENTIFY_ORDER",
    clarify_order=("order_id",),
    nodes={
        "IDENTIFY_ORDER": _ORDER_IDENTIFY,
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("list_orders", "get_order", "get_invoice"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE"),
            after_answer="ANSWERED",
        ),
        "NEEDS_REVALIDATION": _ORDER_IDENTIFY,
    },
)

PLAN_QUERY = FlowDefinition(
    flow_type="PLAN_QUERY",
    skill="plan-service",
    entry_node="ANSWERED",
    clarify_order=(),
    nodes={
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("search_plans", "get_plan"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE"),
            after_answer="ANSWERED",
        ),
        "NEEDS_REVALIDATION": NodeDefinition(
            node="NEEDS_REVALIDATION",
            required_slots=(),
            allowed_tools=("search_plans", "get_plan"),
            allowed_next_actions=("QUERY_BUSINESS", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
            after_query="ANSWERED",
        ),
    },
)

POLICY_QUERY = FlowDefinition(
    flow_type="POLICY_QUERY",
    skill="policy-service",
    entry_node="ANSWERED",
    clarify_order=(),
    nodes={
        "ANSWERED": NodeDefinition(
            node="ANSWERED",
            required_slots=(),
            allowed_tools=("search_knowledge", "search_plans"),
            allowed_next_actions=("QUERY_POLICY", "ASK_CLARIFY", "ANSWER", "HANDOFF", "DECLINE"),
            after_answer="ANSWERED",
        ),
        "NEEDS_REVALIDATION": NodeDefinition(
            node="NEEDS_REVALIDATION",
            required_slots=(),
            allowed_tools=("search_knowledge",),
            allowed_next_actions=("QUERY_POLICY", "ASK_CLARIFY", "HANDOFF", "DECLINE"),
            after_query="ANSWERED",
        ),
    },
)


class FlowCatalog:
    """按意图索引流程定义。"""

    def __init__(self, flows: tuple[FlowDefinition, ...]):
        self.flows = {flow.flow_type: flow for flow in flows}

    def get(self, flow_type: str) -> FlowDefinition | None:
        return self.flows.get(flow_type)

    def require(self, flow_type: str) -> FlowDefinition:
        flow = self.get(flow_type)
        if flow is None:
            raise KeyError(f"没有为意图 {flow_type} 定义流程")
        return flow

    def all(self) -> tuple[FlowDefinition, ...]:
        return tuple(self.flows.values())


FLOW_CATALOG = FlowCatalog(
    (
        REFUND_APPLY,
        INVOICE_APPLY,
        CANCEL_ORDER,
        ORDER_QUERY,
        SERVICE_PROGRESS_QUERY,
        AFTER_SALE_QUERY,
        INVOICE_QUERY,
        PLAN_QUERY,
        POLICY_QUERY,
    )
)
