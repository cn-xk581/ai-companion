"""FastAPI 鉴权依赖：解析 Bearer JWT -> 查库 -> AuthUser（含状态校验）。"""
from __future__ import annotations

from dataclasses import dataclass

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_access_token
from app.config import get_settings
from app.storage.postgres import STATUS_BANNED, STATUS_DELETED, STATUS_MUTED

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    user_id: int
    username: str
    nickname: str = ""
    avatar: str = ""
    status: int = 1
    is_creator: bool = False

    @property
    def display_name(self) -> str:
        return self.nickname or self.username


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    settings = get_settings()
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="缺少认证信息（Authorization: Bearer <token>）"
        )
    try:
        payload = decode_access_token(
            credentials.credentials, settings.jwt_secret, settings.jwt_algorithm
        )
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="token 无效或已过期")
    raw_id = payload.get("sub")
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="token 缺少有效用户标识")

    repo = request.app.state.ctx.repo
    user = await repo.get_user(user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="账号不存在或已注销")
    if user.status in (STATUS_BANNED, STATUS_DELETED):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号已被封禁或注销")
    return AuthUser(
        user_id=user.id,
        username=user.username,
        nickname=user.nickname or user.username,
        avatar=user.avatar,
        status=user.status,
        is_creator=user.is_creator,
    )


def ensure_can_chat(user: AuthUser) -> None:
    """禁言用户仍可登录浏览，但不能发消息。"""
    if user.status == STATUS_MUTED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号当前处于禁言状态，暂时不能发消息")
