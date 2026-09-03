"""FastAPI 鉴权依赖：解析 Authorization: Bearer <JWT> -> AuthUser。"""
from __future__ import annotations

from dataclasses import dataclass

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_access_token
from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    user_id: str
    username: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    settings = get_settings()
    if credentials is None or not credentials.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="缺少认证信息（Authorization: Bearer <token>）")
    try:
        payload = decode_access_token(credentials.credentials, settings.jwt_secret, settings.jwt_algorithm)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="token 无效或已过期")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="token 缺少用户标识")
    return AuthUser(user_id=user_id, username=payload.get("username", ""))
