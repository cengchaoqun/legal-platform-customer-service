from typing import Any

import httpx

from atguigu.common.config import Settings, get_settings


class BusinessRejection(Exception):
    """业务服务明确拒绝了请求（无权限、不存在、状态不允许）。

    这类失败不应盲目重试，也不属于服务不可用；工具层会把它归类为 BUSINESS。
    """

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LegalPlatformClient:
    """使用当前客户身份访问法律平台（或演示业务服务）的只读接口。

    令牌只放在请求头，不进入工具参数、提示词或审计记录。
    """

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.settings = get_settings()

    async def get_json(
        self, resource_path: str, query_params: dict[str, Any] | None = None
    ) -> Any:
        request_url = (
            f"{self.settings.legal_platform_base_url.rstrip('/')}/"
            f"{resource_path.lstrip('/')}"
        )
        request_headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(
            timeout=self.settings.legal_platform_timeout_seconds, trust_env=False
        ) as http_client:
            response = await http_client.get(
                request_url, headers=request_headers, params=query_params
            )

            if response.status_code >= 500:
                # 服务端错误交由执行器归类为 SERVICE_CALL，可有限重试
                response.raise_for_status()

            if response.status_code >= 400:
                code, message = self._parse_error(response)
                if response.status_code in (401, 403, 404, 409, 422):
                    raise BusinessRejection(code, message, response.status_code)
                response.raise_for_status()

            payload = response.json()
            # 业务服务约定 HTTP 200 也可能是 success=false
            if isinstance(payload, dict) and payload.get("success") is False:
                raise BusinessRejection(
                    str(payload.get("code") or "BUSINESS_REJECTED"),
                    str(payload.get("message") or "业务请求未被受理"),
                    response.status_code,
                )
            return payload

    @staticmethod
    def _parse_error(response: httpx.Response) -> tuple[str, str]:
        code = "SERVICE_REJECTED"
        message = response.text
        try:
            body = response.json()
            if isinstance(body, dict):
                code = str(body.get("code") or code)
                message = str(body.get("message") or message)
        except ValueError:
            pass
        return code, message


# 兼容旧命名：原电商客户端已整体替换为法律平台客户端
EcommerceClient = LegalPlatformClient
