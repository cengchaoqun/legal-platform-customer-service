from atguigu.agent.harness.skills.definition import SkillCode, SkillDefinition


class SkillCatalog:
    """集中保存 Agent 可以按需加载的领域技能。"""

    def __init__(self, definitions: tuple[SkillDefinition, ...]):
        self.definitions = {definition.code: definition for definition in definitions}
        self.by_code = {definition.code.value: definition for definition in definitions}

    def get_skill(self, skill_code: SkillCode) -> SkillDefinition:
        return self.definitions[skill_code]

    def get_by_code(self, code: str) -> SkillDefinition:
        """按字符串编码读取，供流程定义引用。"""
        return self.by_code[code]

    def get_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self.definitions.values())

    def render_index(self) -> str:
        return "\n".join(
            f"- {skill.code}: {skill.description}" for skill in self.definitions.values()
        )


SKILL_CATALOG = SkillCatalog(
    (
        SkillDefinition(
            code=SkillCode.PLAN_SERVICE,
            description="法律服务套餐的价格、权益、适用范围和有效期",
            guidance=(
                "用户没有明确套餐时，先用 search_plans 按需求或名称检索。",
                "价格、权益、有效期只能来自 search_plans 或 get_plan 的成功结果。",
                "不承诺服务效果，不把套餐描述扩展成法律意见。",
                "套餐咨询只做说明，不生成购买或支付动作。",
            ),
            tools=("search_plans", "get_plan"),
        ),
        SkillDefinition(
            code=SkillCode.ORDER_SERVICE,
            description="订单查询、支付与履约状态、开票申请与开票进度",
            guidance=(
                "用户没有提供订单号时，先用 list_orders 查询，唯一匹配则直接使用。",
                "订单事实只能来自 get_order；开票状态只能来自 get_invoice。",
                "开票动作只提供页面入口，不得声称发票已开具。",
                "只有用户明确要求开票且订单允许时，才提供 APPLY_INVOICE 页面动作。",
            ),
            tools=("list_orders", "get_order", "get_invoice"),
        ),
        SkillDefinition(
            code=SkillCode.PROGRESS_SERVICE,
            description="合同审核、法律咨询、文书起草等服务办理进度与材料缺失",
            guidance=(
                "用户没有提供订单号时，先用 list_orders 查询，唯一匹配则直接使用。",
                "先确认订单，再查询办理进度。",
                "节点、时间、材料缺失项只能来自 get_service_progress 的成功结果。",
                "没有预计完成时间时说明“以服务更新为准”，不自行推测日期。",
                "不展示或推断内部案情、律师意见与审核结论。",
            ),
            tools=("list_orders", "get_order", "get_service_progress"),
        ),
        SkillDefinition(
            code=SkillCode.AFTER_SALES_SERVICE,
            description="退款申请、取消申请、售后进度与退款到账状态",
            guidance=(
                "用户没有提供订单号时，先用 list_orders 查询，唯一匹配则直接使用。",
                "区分“我要退款”（申请）与“退款到哪了”（进度查询）。",
                "申请资格以 get_order 与 search_knowledge 的政策为参考，最终由业务页面校验。",
                "申请状态与退款到账是不同状态：申请已提交不等于审核通过，更不等于已到账。",
                "退款申请只提供 APPLY_AFTER_SALE 页面入口，不得代替用户提交。",
            ),
            tools=("list_orders", "get_order", "list_after_sales", "search_knowledge"),
        ),
        SkillDefinition(
            code=SkillCode.POLICY_SERVICE,
            description="平台服务政策：开票、退款、材料提交、服务时段与咨询渠道",
            guidance=(
                "平台规则必须来自 search_knowledge，不得凭常识补充时限或费用。",
                "政策条目有适用套餐与生效日期，跨套餐时说明适用范围。",
                "没有匹配条目时说明无法确认，并提供人工入口，不编造规则。",
                "具体案情判断与诉讼策略不属于客服范围，应转专业渠道。",
            ),
            tools=("search_knowledge",),
        ),
    )
)
