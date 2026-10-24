from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.models.models import ConversationFlowState


class FlowStateRepository:
    """流程状态的数据访问。

    只有加锁读取才允许用于结算；普通读取用于构建 AI 请求快照。
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_by_conversation_id(self, conversation_id: str) -> ConversationFlowState | None:
        return await self.session.scalar(
            select(ConversationFlowState).where(
                ConversationFlowState.conversation_id == conversation_id
            )
        )

    async def lock_by_conversation_id(self, conversation_id: str) -> ConversationFlowState | None:
        return await self.session.scalar(
            select(ConversationFlowState)
            .where(ConversationFlowState.conversation_id == conversation_id)
            .with_for_update()
        )

    def add(self, state: ConversationFlowState) -> None:
        self.session.add(state)
