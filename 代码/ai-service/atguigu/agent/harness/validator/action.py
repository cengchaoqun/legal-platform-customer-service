from atguigu.agent.harness.errors import AgentOutputValidationError, CorrectableErrorCode
from atguigu.agent.harness.rules.action import ACTION_CATALOG, PageAction
from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot
from atguigu.agent.llm.output import PageActionRequest

# 页面动作与订单允许操作的对应关系：查到订单不等于允许办理
ACTION_REQUIRES_ORDER_PERMISSION = {
    "CANCEL_ORDER": "CANCEL_ORDER",
    "APPLY_INVOICE": "APPLY_INVOICE",
    "APPLY_AFTER_SALE": "APPLY_AFTER_SALE",
}


class PageActionValidationError(AgentOutputValidationError):
    """表示 Agent 提出的页面动作不符合安全规则。"""


class PageActionValidator:
    """校验页面动作编码、节点范围、资源归属与业务资格。"""

    def validate(
        self,
        request: PageActionRequest | None,
        tool_calls: tuple[ToolCallSnapshot, ...],
        *,
        allowed_action_codes: list[str] | None = None,
    ) -> PageAction | None:
        if request is None:
            return None

        action_code = request.action_code
        if not ACTION_CATALOG.has(action_code.value):
            raise PageActionValidationError(
                CorrectableErrorCode.UNVERIFIED_ACTION_RESOURCE,
                f"未知页面动作：{action_code.value}",
            )

        # 1. 当前流程节点是否允许该动作
        if allowed_action_codes is not None and action_code.value not in allowed_action_codes:
            raise PageActionValidationError(
                CorrectableErrorCode.UNVERIFIED_ACTION_RESOURCE,
                f"当前流程节点不允许页面动作：{action_code.value}",
            )

        definition = ACTION_CATALOG.get_definition(action_code)

        # 2. 列表页动作不绑定具体订单
        if definition.resource_type is None:
            return definition.build()

        resource_id = self._normalize_resource_id(request.resource_id or "")

        # 3. 必须在本 Run 成功查询过该订单
        order_snapshot = self._find_verified_order(resource_id, tool_calls)
        if order_snapshot is None:
            raise PageActionValidationError(
                CorrectableErrorCode.UNVERIFIED_ACTION_RESOURCE,
                f"页面动作引用的订单未经本轮工具确认：{resource_id}。请先调用 get_order(order_id='{resource_id}')；历史回复、进度和发票结果不能替代订单权限证据，无法核实时删除 page_action_request。",
            )

        # 4. 写操作类动作还要满足业务返回的 allowed_actions
        required_permission = ACTION_REQUIRES_ORDER_PERMISSION.get(action_code.value)
        if required_permission:
            order_data = (order_snapshot.result or {}).get("data") or {}
            allowed = order_data.get("allowed_actions") or []
            if required_permission not in allowed:
                raise PageActionValidationError(
                    CorrectableErrorCode.UNVERIFIED_ACTION_RESOURCE,
                    f"订单当前状态不允许 {required_permission}，请先说明实际状态并刷新页面",
                )

        return definition.build(resource_id)

    @staticmethod
    def _find_verified_order(
        resource_id: str, tool_calls: tuple[ToolCallSnapshot, ...]
    ) -> ToolCallSnapshot | None:
        for tool_call in tool_calls:
            if tool_call.tool_name != "get_order" or tool_call.success is not True:
                continue
            argument_id = PageActionValidator._normalize_resource_id(
                str(tool_call.arguments.get("order_id", ""))
            )
            if argument_id != resource_id:
                continue
            data = (tool_call.result or {}).get("data") or {}
            if str(data.get("id", "")).upper() != resource_id:
                continue
            return tool_call
        return None

    @staticmethod
    def _normalize_resource_id(resource_id: str) -> str:
        return resource_id.strip().upper().replace("-", "_")
