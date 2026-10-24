from dataclasses import dataclass


@dataclass(frozen=True)
class NodeDefinition:
    """一个流程节点的允许范围。

    `allowed_tools` 是该节点允许调用的业务工具，执行器入口会再校验一次；
    `page_action` 只在确实需要用户到页面确认时存在。
    """

    node: str
    required_slots: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    allowed_next_actions: tuple[str, ...]
    page_action: str | None = None
    # 查询成功后进入的节点
    after_query: str | None = None
    # 回答/转人工后进入的节点
    after_answer: str | None = None


@dataclass(frozen=True)
class FlowDefinition:
    flow_type: str
    skill: str
    entry_node: str
    # 澄清时按顺序检查的关键槽位，一次只问一个
    clarify_order: tuple[str, ...]
    nodes: dict[str, NodeDefinition]
    # 进入流程时需要先确认的业务对象
    identify_tools: tuple[str, ...] = ("list_orders",)

    def node(self, name: str) -> NodeDefinition:
        return self.nodes[name]

    def has_node(self, name: str) -> bool:
        return name in self.nodes

    def clarify_node(self) -> str:
        return self.entry_node
