from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.common.utils import get_utcnow
from atguigu.models.models import RealtimeOutbox


class RealTimeOutboxRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def add(self, event: RealtimeOutbox):
        """将实时事件加入当前数据库事务"""
        self.session.add(event)

    async def find_next_unpublished(self) -> RealtimeOutbox | None:
        """按创建顺序查询一条尚未发布的事件。

        使用 SKIP LOCKED：多个 Outbox Worker 不会重复领取同一条事件；
        发布失败时事件保持未发布，由下一轮重试。
        """
        return await self.session.scalar(
            select(RealtimeOutbox)
            .where(RealtimeOutbox.published_at.is_(None))
            .order_by(RealtimeOutbox.sequence)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    @staticmethod
    def mark_publish_succeeded(event: RealtimeOutbox):
        """记录发布成功。"""
        event.attempts += 1
        event.published_at = get_utcnow()

    @staticmethod
    def mark_publish_failed(event: RealtimeOutbox):
        """记录失败次数，使事件保留为待发布状态"""
        event.attempts += 1
