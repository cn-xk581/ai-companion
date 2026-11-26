"""鉴权路由：注册 / 登录 / 刷新 / 登出 / 当前用户。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.deps import AuthUser, get_current_user
from app.auth.rate_limit import (
    clear_login_failures,
    client_ip,
    enforce_auth_rate_limit,
    is_login_locked,
    record_login_failure,
)
from app.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    OkResponse,
    ProfileModel,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from app.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.storage.postgres import (
    STATUS_BANNED,
    STATUS_DELETED,
    DuplicateUserError,
    UserRecord,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 保留名：避免冒充官方
RESERVED_USERNAMES = {"admin", "root", "system", "official", "support", "help", "moderator"}


def _public(user: UserRecord) -> UserPublic:
    return UserPublic(
        user_id=user.id,
        username=user.username,
        nickname=user.nickname or user.username,
        avatar=user.avatar,
        is_creator=user.is_creator,
        status=user.status,
    )


def _device(request: Request) -> tuple[str, str]:
    platform = (request.headers.get("x-client-platform") or "web")[:16]
    device_id = (request.headers.get("x-device-id") or "")[:128]
    return device_id, platform


async def _issue(ctx, user: UserRecord, request: Request) -> TokenResponse:
    device_id, platform = _device(request)
    access = create_access_token(
        user.id,
        user.username,
        ctx.settings.jwt_secret,
        ctx.settings.jwt_algorithm,
        ctx.settings.access_token_minutes,
    )
    refresh = create_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=ctx.settings.refresh_token_days)
    await ctx.repo.create_auth(
        user.id,
        hash_refresh_token(refresh),
        device_id=device_id,
        platform=platform,
        client_ip=client_ip(request, ctx.settings.trust_proxy_headers),
        user_agent=request.headers.get("user-agent", ""),
        expires_at=expires_at,
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=ctx.settings.access_token_minutes * 60,
        user=_public(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, request: Request):
    ctx = request.app.state.ctx
    await enforce_auth_rate_limit(ctx, client_ip(request, ctx.settings.trust_proxy_headers))
    username = req.username.strip().lower()
    if username in RESERVED_USERNAMES or username.startswith("official_"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="该用户名为保留名，请换一个")
    try:
        user = await ctx.repo.create_user(
            username, hash_password(req.password), nickname=req.nickname.strip() or username
        )
    except DuplicateUserError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="用户名已存在")
    await ctx.repo.touch_login(user.id)
    return await _issue(ctx, user, request)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    ctx = request.app.state.ctx
    await enforce_auth_rate_limit(ctx, client_ip(request, ctx.settings.trust_proxy_headers))
    username = req.username.strip().lower()

    if await is_login_locked(ctx, username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="登录失败次数过多，请稍后再试")

    user = await ctx.repo.get_user_by_username(username)
    if user is None or not verify_password(req.password, user.password_hash):
        await record_login_failure(ctx, username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if user.status in (STATUS_BANNED, STATUS_DELETED):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号已被封禁或注销")

    await clear_login_failures(ctx, username)
    # 旧 PBKDF2 或参数过弱的哈希：登录成功后惰性升级为 argon2id
    if needs_rehash(user.password_hash):
        try:
            await ctx.repo.update_user(user.id, password_hash=hash_password(req.password))
        except Exception:  # noqa: BLE001 - 升级失败不影响本次登录
            pass
    await ctx.repo.touch_login(user.id)
    return await _issue(ctx, user, request)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, request: Request):
    ctx = request.app.state.ctx
    token_hash = hash_refresh_token(req.refresh_token)
    row = await ctx.repo.find_auth(token_hash)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token 无效")
    if row.get("revoked_at") is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token 已失效，请重新登录")
    expires_at = row.get("expires_at")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token 已过期，请重新登录")

    user = await ctx.repo.get_user(int(row["user_id"]))
    if user is None or user.status in (STATUS_BANNED, STATUS_DELETED):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号不可用")

    # 轮换：旧的立刻作废，新的接着用
    await ctx.repo.revoke_auth(int(row["id"]), "rotated")
    await ctx.repo.touch_auth(int(row["id"]))
    return await _issue(ctx, user, request)


@router.post("/logout", response_model=OkResponse)
async def logout(req: LogoutRequest, request: Request, user: AuthUser = Depends(get_current_user)):
    ctx = request.app.state.ctx
    if req.all_devices:
        await ctx.repo.revoke_user_auths(user.user_id, "logout_all")
    elif req.refresh_token:
        row = await ctx.repo.find_auth(hash_refresh_token(req.refresh_token))
        if row is not None and int(row["user_id"]) == user.user_id:
            await ctx.repo.revoke_auth(int(row["id"]), "logout")
    return OkResponse()


@router.get("/me", response_model=MeResponse)
async def me(request: Request, user: AuthUser = Depends(get_current_user)):
    ctx = request.app.state.ctx
    record = await ctx.repo.get_user(user.user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    profile = await ctx.repo.get_profile(user.user_id)
    return MeResponse(user=_public(record), profile=ProfileModel(**profile))
