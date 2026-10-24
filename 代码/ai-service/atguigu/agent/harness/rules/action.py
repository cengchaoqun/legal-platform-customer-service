from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

from pydantic import BaseModel


class ActionCode(StrEnum):
    """服务端允许生成的页面动作编码。

    AI 只给动作码与资源编号，href 由服务端固定模板生成，模型不能构造任意 URL。
    """

    BROWSE_ORDERS = "BROWSE_ORDERS"
    OPEN_ORDER_DETAIL = "OPEN_ORDER_DETAIL"
    CANCEL_ORDER = "CANCEL_ORDER"
    APPLY_INVOICE = "APPLY_INVOICE"
    APPLY_AFTER_SALE = "APPLY_AFTER_SALE"
    VIEW_AFTER_SALES = "VIEW_AFTER_SALES"


class PageAction(BaseModel):
    """返回给前端的可信页面动作。"""

    action_code: str
    label: str
    description: str
    href: str


@dataclass(frozen=True)
class ActionDefinition:
    action_code: ActionCode
    label: str
    description: str
    path_template: str
    resource_type: str | None = None

    def build(self, resource_id: str | None = None) -> PageAction:
        encoded = quote(resource_id or "", safe="")
        return PageAction(
            action_code=self.action_code.value,
            label=self.label,
            description=self.description,
            href=self.path_template.format(resource_id=encoded),
        )


class ActionCatalog:
    """集中保存服务端允许生成的页面动作。"""

    def __init__(self, definitions: tuple[ActionDefinition, ...]):
        self.definitions = {definition.action_code: definition for definition in definitions}

    def get_definition(self, action_code: ActionCode) -> ActionDefinition:
        return self.definitions[action_code]

    def has(self, action_code: str) -> bool:
        return action_code in {code.value for code in self.definitions}

    def render_action_index(self) -> str:
        return "\n".join(
            f"- {definition.action_code}: {definition.description}"
            for definition in self.definitions.values()
        )

    def build_page_action(
        self, action_code: ActionCode, resource_id: str | None = None
    ) -> PageAction:
        return self.get_definition(action_code).build(resource_id)


ACTION_CATALOG = ActionCatalog(
    (
        ActionDefinition(
            action_code=ActionCode.BROWSE_ORDERS,
            label="查看我的订单",
            description="进入法律服务订单列表",
            path_template="/me",
        ),
        ActionDefinition(
            action_code=ActionCode.OPEN_ORDER_DETAIL,
            label="查看订单详情",
            description="进入订单详情查看办理进度、发票与售后",
            path_template="/me/orders/{resource_id}",
            resource_type="order",
        ),
        ActionDefinition(
            action_code=ActionCode.CANCEL_ORDER,
            label="前往取消订单",
            description="进入订单页面核对待退款状态并确认取消",
            path_template="/me/orders/{resource_id}?action=cancel",
            resource_type="order",
        ),
        ActionDefinition(
            action_code=ActionCode.APPLY_INVOICE,
            label="申请开票",
            description="进入开票申请页面填写抬头资料并提交",
            path_template="/me/orders/{resource_id}/invoice",
            resource_type="order",
        ),
        ActionDefinition(
            action_code=ActionCode.APPLY_AFTER_SALE,
            label="申请退款 / 售后",
            description="进入退款申请页面填写原因并提交，需人工审核",
            path_template="/me/orders/{resource_id}/refund",
            resource_type="order",
        ),
        ActionDefinition(
            action_code=ActionCode.VIEW_AFTER_SALES,
            label="查看退款进度",
            description="进入订单详情查看售后申请与退款状态",
            path_template="/me/orders/{resource_id}?section=after-sales",
            resource_type="order",
        ),
    )
)
