from datetime import date

from pydantic import BaseModel, Field


class KnowledgeItem(BaseModel):
    """一条可追溯的版本化服务政策。"""

    knowledge_id: str = Field(min_length=1, description="知识内容的稳定编号")
    title: str = Field(min_length=1, description="知识内容标题")
    content: str = Field(min_length=1, description="Agent 可以用于回答的知识正文")
    category: str = Field(default="POLICY", description="政策分类")
    version: str = Field(default="policy-v1", description="政策版本")
    effective_from: date | None = Field(default=None, description="生效日期")
    effective_to: date | None = Field(default=None, description="失效日期，空表示长期有效")
    applicable_plans: list[str] = Field(
        default_factory=list, description="适用套餐；空表示全部套餐"
    )
    source: str = Field(min_length=1, description="知识内容的来源说明")
    status: str = Field(default="ACTIVE", description="条目状态")
