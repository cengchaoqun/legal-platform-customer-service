import asyncio
import logging
from typing import Any

import httpx

from atguigu.common.config import Settings, get_settings

logger = logging.getLogger(__name__)


class AIServiceError(RuntimeError):
    """AI Service 返回了不可重试的业务或协议错误。"""

    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AIServiceGateway:
    """调用 AI Service，并处理幂等重试与运行中冲突。"""

    # 单次 HTTP 传输重试次数：保持同一 request_id，由 AI 侧幂等返回原结果
    TRANSPORT_RETRIES = 2
    # 收到 RUN_IN_PROGRESS 时在预算内等待
    RUN_IN_PROGRESS_RETRIES = 3

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def start_run(self, token: str, request: dict[str, Any]) -> dict[str, Any]:
        """启动 AI Run；同一 request_id 重试返回原事件。"""
        attempt = 0
        while True:
            try:
                return await self._post_json("/internal/v1/agent/runs", token, request)
            except AIServiceError as exc:
                if exc.code == "RUN_IN_PROGRESS" and attempt < self.RUN_IN_PROGRESS_RETRIES:
                    attempt += 1
                    await asyncio.sleep(0.6 * attempt)
                    continue
                raise

    async def commit_run(self, token: str, run_id: str, input_revision: int) -> dict[str, Any]:
        """接纳 AI 准备的页面动作结果。

        语义是“Customer 接纳 AI 准备的结果”，不代表用户已经提交申请。
        """
        return await self._post_json(
            f"/internal/v1/agent/runs/{run_id}/commit",
            token,
            {"input_revision": input_revision},
        )

    async def cancel_run(self, token: str, run_id: str) -> None:
        """取消已准备但快照过期的 Run。"""
        await self._request("POST", f"/internal/v1/agent/runs/{run_id}/cancel", token)

    async def record_customer_outcome(
        self,
        token: str,
        run_id: str,
        *,
        outcome: str,
        turn_id: str,
        input_revision: int,
        reason: str | None = None,
    ) -> bool:
        """回传 Customer 是否最终接纳该 Run。

        回执失败不回滚已提交的会话事务；AI 管理端保留 UNKNOWN，之后由对账命令补发。
        """
        try:
            await self._post_json(
                f"/internal/v1/agent/runs/{run_id}/customer-outcome",
                token,
                {
                    "outcome": outcome,
                    "turn_id": turn_id,
                    "input_revision": input_revision,
                    "reason": reason,
                },
            )
            return True
        except Exception:  # noqa: BLE001 - 回执失败不应影响已完成的业务事务
            logger.warning("上报 customer_outcome 失败 run=%s", run_id, exc_info=True)
            return False

    async def _post_json(
        self, path: str, token: str, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self._request("POST", path, token, request)
        if response.status_code == 204 or not response.content:
            return {}
        event = response.json()
        if not isinstance(event, dict):
            raise AIServiceError(response.status_code, "AI Service 返回的事件格式错误")
        return event

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        request: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Internal-Service-Token": self.settings.internal_service_token,
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(self.settings.ai_timeout_seconds, connect=5)
        last_error: Exception | None = None

        for attempt in range(self.TRANSPORT_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    response = await client.request(
                        method,
                        f"{self.settings.ai_service_url}{path}",
                        headers=headers,
                        json=request,
                    )
                return self._raise_for_status(response)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt + 1 < self.TRANSPORT_RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
                raise RuntimeError(f"AI Service 网络调用失败：{exc}") from exc

        raise RuntimeError(f"AI Service 网络调用失败：{last_error}")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> httpx.Response:
        if response.is_success:
            return response
        code = None
        message = response.text
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = body.get("detail")
                if isinstance(detail, dict):
                    code = detail.get("code")
                    message = str(detail.get("message") or message)
                elif isinstance(detail, str):
                    message = detail
                code = code or body.get("code")
        except ValueError:
            pass
        raise AIServiceError(response.status_code, message, code)
