from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atguigu.agent.harness.flows.contract import FlowSnapshot


class CurrentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=80)
    type: Literal["text", "object"]
    content: dict[str, Any]


class HistoryMessage(CurrentMessage):
    role: Literal["user", "ai", "human"]
    created_at: datetime | None = None


class AgentRunRequest(BaseModel):
    """AgentRunRequest V2。

    新增字段显式声明并严格校验，未知字段按契约拒绝；服务升级期间
    先部署能读 V1/V2 的消费者，再启用 V2 生产者。
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["legal-support-v2"] = "legal-support-v2"
    request_id: str = Field(min_length=8, max_length=120)
    conversation_id: str = Field(min_length=1, max_length=80)
    turn_id: str = Field(min_length=1, max_length=80)
    # 仅用于与可信 JWT 主体交叉核验，不能覆盖鉴权身份
    user_id: str = Field(min_length=1, max_length=80)
    input_revision: int = Field(ge=0)
    flow_state_version: int = Field(ge=0)
    flow_state: FlowSnapshot | None = None
    messages: list[CurrentMessage] = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_versions(self) -> Self:
        if self.flow_state is None:
            if self.flow_state_version != 0:
                raise ValueError("无流程状态时 flow_state_version 必须为 0")
            return self
        if self.flow_state.state_version != self.flow_state_version:
            raise ValueError("flow_state.state_version 与 flow_state_version 不一致")
        if self.flow_state.flow_definition_version != "legal-support-flow-v1":
            raise ValueError("不支持的流程定义版本")
        return self


class CommitRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_revision: int = Field(ge=0)


class CustomerOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["ACCEPTED", "REJECTED"]
    turn_id: str = Field(min_length=1, max_length=80)
    input_revision: int = Field(ge=0)
    reason: str | None = Field(default=None, max_length=120)
