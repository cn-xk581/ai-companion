"""鉴权路由：注册 / 登录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.deps import AuthUser, get_current_user
from app.auth.rate_limit import (
    clear_login_failures,
    client_ip,
    enforce_auth_rate_limit,
    is_login_locked,
    record_login_failure,
)
from app.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.auth.security import create_access_token, hash_password, verify_password
from app.storage.postgres import DuplicateUserError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _issue_token(ctx, user_id: str, username: str) -> TokenResponse:
    token = create_access_token(
        user_id,
        username,
        ctx.settings.jwt_secret,
        ctx.settings.jwt_algorithm,
        ctx.settings.jwt_expire_minutes,
    )
    return TokenResponse(access_token=token, user_id=user_id, username=username)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, request: Request):
    ctx = request.app.state.ctx
    await enforce_auth_rate_limit(ctx, client_ip(request, ctx.settings.trust_proxy_headers))
    try:
        user = await ctx.repo.create_user(req.username, hash_password(req.password))
    except DuplicateUserError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="用户名已存在")
    return _issue_token(ctx, user.id, user.username)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    ctx = request.app.state.ctx
    await enforce_auth_rate_limit(ctx, client_ip(request, ctx.settings.trust_proxy_headers))

    if await is_login_locked(ctx, req.username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="登录失败次数过多，请稍后再试")

    user = await ctx.repo.get_user_by_username(req.username)
    if user is None or not verify_password(req.password, user.password_hash):
        await record_login_failure(ctx, req.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    await clear_login_failures(ctx, req.username)
    return _issue_token(ctx, user.id, user.username)


@router.get("/me", response_model=UserResponse)
async def me(user: AuthUser = Depends(get_current_user)):
    return UserResponse(user_id=user.user_id, username=user.username)
