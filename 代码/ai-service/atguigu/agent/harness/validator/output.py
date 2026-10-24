from pydantic import Field

from atguigu.agent.harness.rules.action import PageAction
from atguigu.agent.harness.tools.snapshot import ToolCallReader
from atguigu.agent.harness.validator.action import PageActionValidator
from atguigu.agent.harness.validator.answer import AnswerValidator
from atguigu.agent.llm.output import AgentOutput


class ValidatedAgentOutput(AgentOutput):
    page_action: PageAction | None = None
    token_usage: dict[str, int] = Field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )


class AgentOutputValidator:

    def __init__(self, tool_call_reader: ToolCallReader):
        self.tool_call_reader = tool_call_reader
        self.answer_validator = AnswerValidator()
        self.page_action_validator = PageActionValidator()

    async def validate(
        self,
        agent_output: AgentOutput,
        run_id: str,
        *,
        allowed_next_actions: list[str] | None = None,
        allowed_action_codes: list[str] | None = None,
        clarify_slot: str | None = None,
        require_knowledge: bool = False,
    ) -> ValidatedAgentOutput:
        tool_calls = await self.tool_call_reader.read_by_run_id(run_id)

        checked = self.answer_validator.validate(
            agent_output,
            tool_calls,
            allowed_next_actions=allowed_next_actions,
            clarify_slot=clarify_slot,
            require_knowledge=require_knowledge,
        )
        page_action = self.page_action_validator.validate(
            checked.page_action_request,
            tool_calls,
            allowed_action_codes=allowed_action_codes,
        )
        return ValidatedAgentOutput.model_validate(
            {**checked.model_dump(), "page_action": page_action}
        )
