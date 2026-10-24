"""Turn(AI) Worker。

职责：领取到期 Turn、构建 V2 请求、调用 AI Service、在结算事务中接纳结果并回执。
"""
import asyncio
import logging
import os
import socket
from typing import Any

from atguigu.app.respositories.chat.conversation import ConversationRepository
from atguigu.app.respositories.chat.turn import ConversationTurnRepository
from atguigu.app.schemas.admin.user import CurrentUser
from atguigu.app.services.admin.auth import AuthService
from atguigu.app.services.chat.turn import ClaimToken, TurnService
from atguigu.common.event_loop import run_async
from atguigu.infrastucture.db import session_factory
from atguigu.worker.ai.gateway import AIServiceGateway
from atguigu.worker.ai.parser import AIEventParser
from atguigu.worker.ai.settlement import TurnSettlementService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
from atguigu.common.config import get_settings  # noqa: E402


class TurnProcessor:
    """一次 Turn 的完整处理流程。"""

    def __init__(self):
        self.setting = get_settings()
        self.auth_service = AuthService()
        self.ai_gateway = AIServiceGateway()
        self.event_parser = AIEventParser()

    async def process(
        self, request_data: dict[str, Any], claim: ClaimToken
    ) -> str:
        # 1. 构造内部令牌：AI Service 用该身份复核请求体中的 user_id
        access_token = self.auth_service.encode_access_token(
            CurrentUser(user_id=request_data["user_id"])
        )

        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(claim, stop_heartbeat))

        error: Exception | None = None
        run_id: str | None = None
        outcome: dict[str, Any] | None = None
        try:
            run_id, outcome = await self.run_ai_pipeline(access_token, request_data, claim)
        except Exception as exc:  # noqa: BLE001 - 统一转为可重试失败
            error = exc
            logger.exception("%s 运行失败,原因:%s", claim.turn_id, exc)
        finally:
            stop_heartbeat.set()
            await asyncio.gather(heartbeat, return_exceptions=True)

        # 2. 结算：校验租约、输入版本、流程版本与会话模式
        disposition = await self._finalize(claim, run_id, outcome, error)

        # 3. 回执：Customer 是否最终接纳，失败不影响已提交事务
        if run_id and disposition in {
            "COMPLETED",
            "FAILED_FALLBACK",
            "SUPERSEDED",
            "LEASE_LOST",
            "FLOW_PATCH_REJECTED",
        }:
            accepted = disposition == "COMPLETED"
            await self.ai_gateway.record_customer_outcome(
                access_token,
                run_id,
                outcome="ACCEPTED" if accepted else "REJECTED",
                turn_id=claim.turn_id,
                input_revision=claim.snapshot_revision,
                reason=None if accepted else disposition,
            )
        return disposition

    async def _finalize(
        self,
        claim: ClaimToken,
        run_id: str | None,
        outcome: dict[str, Any] | None,
        error: Exception | None,
    ) -> str:
        async with session_factory() as session:
            service = TurnSettlementService(session)
            try:
                return await service.finalize_turn(
                    claim, run_id=run_id, outcome=outcome, error=error
                )
            except Exception:
                await session.rollback()
                logger.exception("Turn 结算失败 turn=%s", claim.turn_id)
                return "SETTLEMENT_ERROR"

    async def run_ai_pipeline(
        self, token: str, request_data: dict[str, Any], claim: ClaimToken
    ) -> tuple[str | None, dict[str, Any] | None]:
        """两阶段调用：start_run → 可选 commit。

        prepared 只表示 AI 准备了页面动作结果；commit 的含义是接纳准备结果，
        不代表用户已经提交申请或退款到账。
        """
        event = await self.ai_gateway.start_run(token, request_data)
        run_id = self.event_parser.find_run_id(event)

        if not self.event_parser.has_prepared_decision(event):
            return run_id, self.event_parser.parse_outcome(event)

        # 提交前再次确认快照仍然有效，避免把过期页面动作交给用户
        if not await self._is_snapshot_current(claim):
            await self.ai_gateway.cancel_run(token, run_id)
            return run_id, None

        event = await self.ai_gateway.commit_run(
            token, run_id, request_data["input_revision"]
        )
        return run_id, self.event_parser.parse_outcome(event)

    async def _is_snapshot_current(self, claim: ClaimToken) -> bool:
        async with session_factory() as session:
            conversation = await ConversationRepository(session).get_conversation(
                claim.conversation_id
            )
            turn = await ConversationTurnRepository(session).find_by_id(claim.turn_id)
            if conversation is None or turn is None:
                return False
            return (
                conversation.mode == "AI"
                and conversation.input_revision == claim.snapshot_revision
                and turn.status == "RUNNING"
                and turn.locked_by == claim.worker_id
                and turn.attempts == claim.claim_attempt
            )

    async def _heartbeat(self, claim: ClaimToken, stop: asyncio.Event) -> None:
        """运行期间续租，避免长任务被误判为 Worker 中断。"""
        interval = max(5, self.setting.ai_worker_lease_seconds // 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            async with session_factory() as session:
                renewed = await TurnService(session).renew_lease(claim)
            if not renewed:
                logger.warning("租约续期失败，Turn 已被接管 turn=%s", claim.turn_id)
                return


class AIWorker:

    def __init__(self):
        self.setting = get_settings()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.turn_processor = TurnProcessor()
        self.session_factory = session_factory

    async def start(self):
        logger.info("AI Worker 已启动：%s", self.worker_id)
        while True:
            try:
                processed = await self.poll_and_process()
                if not processed:
                    await asyncio.sleep(self.setting.ai_worker_poll_interval_ms / 1000)
            except Exception:
                logger.exception("AI Worker 本轮轮询失败")
                await asyncio.sleep(1)

    async def poll_and_process(self) -> bool:
        await self._recover_expired_turns()
        claimed = await self.claim_turn()
        if claimed is None:
            return False
        request_data, claim = claimed
        disposition = await self.turn_processor.process(request_data, claim)
        logger.info("Turn %s 结算结果=%s", claim.turn_id, disposition)
        return True

    async def claim_turn(self) -> tuple[dict[str, Any], ClaimToken] | None:
        """领取 Turn 并构建请求，随后立即提交短事务释放数据库锁。"""
        async with self.session_factory() as session:
            turn_service = TurnService(session)
            claimed = await turn_service.claim_turn(self.worker_id)
            if claimed is None:
                return None

            turn, claim = claimed
            request_data, _ = await turn_service.build_ai_request_data(turn, claim)
            await session.commit()
            return request_data, claim

    async def _recover_expired_turns(self) -> None:
        """回收 Worker 中断遗留的过期租约，不重复生成消息或工单。"""
        async with self.session_factory() as session:
            turn_service = TurnService(session)
            pairs = await turn_service.list_expired_running_turns_with_conversations()
            for turn, conversation in pairs:
                if conversation.input_revision != turn.snapshot_revision:
                    turn_service.mark_superseded(turn, "INPUT_SUPERSEDED")
                else:
                    turn_service.requeue(turn, RuntimeError("Worker 租约超时"))
            if pairs:
                await session.commit()


async def main_async():
    await AIWorker().start()


if __name__ == "__main__":
    run_async(main_async())
