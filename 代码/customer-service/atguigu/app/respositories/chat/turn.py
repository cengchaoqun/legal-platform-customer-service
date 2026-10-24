from datetime import datetime

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from atguigu.models.models import Conversation, ConversationTurn


class ConversationTurnRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_activate_turn(self, conversation_id: str) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        ConversationTurn.conversation_id == conversation_id,
                        ConversationTurn.status.in_(["COLLECTING", "RUNNING"]),
                    )
                )
            )
        )

    async def find_conversation_by_id(self, conv_id: str) -> ConversationTurn | None:
        """查询会话当前收集中的 Turn，并锁定该行。"""
        return await self.session.scalar(
            select(ConversationTurn)
            .where(
                ConversationTurn.conversation_id == conv_id,
                ConversationTurn.status == "COLLECTING",
            )
            .with_for_update()
        )

    def add_turn(self, turn: ConversationTurn):
        self.session.add(turn)

    async def find_by_id(self, turn_id: str) -> ConversationTurn | None:
        return await self.session.get(ConversationTurn, turn_id)

    async def get_and_lock_by_id(self, turn_id: str) -> ConversationTurn | None:
        """结算时按主键锁定 Turn，避免与其它修改入口交错。"""
        return await self.session.scalar(
            select(ConversationTurn)
            .where(ConversationTurn.id == turn_id)
            .with_for_update()
        )

    async def find_turn_conversation_by_id(self, turn_id: str) -> tuple[ConversationTurn, Conversation]:
        result = await self.session.execute(
            select(ConversationTurn, Conversation)
            .join(Conversation, Conversation.id == ConversationTurn.conversation_id)
            .where(ConversationTurn.id == turn_id)
            .with_for_update(of=Conversation)
        )
        return result.tuples().one()

    async def list_expired_running_turns(self, now: datetime) -> list[ConversationTurn]:
        """查询租约已经过期的全部运行中 Turn。"""
        result = await self.session.scalars(
            select(ConversationTurn).where(
                ConversationTurn.status == "RUNNING",
                ConversationTurn.locked_until <= now,
            )
        )
        return list(result.all())

    async def find_ready_turn_conversation_by_lock(
        self, now: datetime
    ) -> tuple[ConversationTurn, Conversation] | None:
        """领取到期 Turn。

        同时满足：COLLECTING、已到期、会话为 AI 模式、同会话没有 RUNNING Turn。
        使用 SKIP LOCKED 让多个 Worker 并行领取不同会话而不互相阻塞。
        """
        running_turn = aliased(ConversationTurn)
        results = await self.session.execute(
            select(ConversationTurn, Conversation)
            .join(Conversation, ConversationTurn.conversation_id == Conversation.id)
            .where(
                ConversationTurn.status == "COLLECTING",
                ConversationTurn.collect_until <= now,
                Conversation.mode == "AI",
                ~exists(
                    select(1).where(
                        and_(
                            running_turn.conversation_id == ConversationTurn.conversation_id,
                            running_turn.status == "RUNNING",
                        )
                    )
                ),
            )
            .order_by(ConversationTurn.collect_until, ConversationTurn.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return results.tuples().one_or_none()
