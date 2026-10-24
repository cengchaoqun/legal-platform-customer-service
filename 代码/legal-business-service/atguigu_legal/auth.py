from datetime import timedelta

import jwt
from fastapi import HTTPException, status

from atguigu_legal.config import get_settings
from atguigu_legal.utils import get_utcnow


class AuthService:
    """演示身份服务。

    生产环境应由法律平台签发令牌；本服务只做演示账号与角色校验，
    并在每次查询时用令牌主体限定数据归属，避免用订单号冒充身份。
    """

    def __init__(self):
        self.settings = get_settings()

    def issue_demo_token(self, user_id: str, role: str, display_name: str) -> tuple[str, int]:
        expires_in = self.settings.demo_token_expire_minutes * 60
        payload = {
            "user_id": user_id,
            "role": role,
            "display_name": display_name,
            "exp": get_utcnow() + timedelta(seconds=expires_in),
        }
        token = jwt.encode(
            payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm
        )
        return token, expires_in

    def get_principal(self, authorization: str | None, *roles: str) -> dict:
        """校验令牌与角色，返回可信主体。"""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="该用户未认证"
            )
        token = authorization.split(" ", 1)[1]
        try:
            payload = jwt.decode(
                token, self.settings.jwt_secret, algorithms=[self.settings.jwt_algorithm]
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效或已过期"
            ) from exc

        if roles and payload.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="用户访问无权限"
            )
        return payload
