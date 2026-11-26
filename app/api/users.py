"""用户资料与头像（仅本人可读写）。"""
from __future__ import annotations

import io
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.auth.deps import AuthUser, get_current_user
from app.auth.schemas import MeResponse, NicknameUpdate, OkResponse, ProfileModel, UserPublic

router = APIRouter(prefix="/api/v1/users", tags=["users"])

_ALLOWED = {"JPEG", "PNG"}


def _public(user) -> UserPublic:
    return UserPublic(
        user_id=user.id,
        username=user.username,
        nickname=user.nickname or user.username,
        avatar=user.avatar,
        is_creator=user.is_creator,
        status=user.status,
    )


@router.get("/me", response_model=MeResponse)
async def me(request: Request, user: AuthUser = Depends(get_current_user)):
    ctx = request.app.state.ctx
    record = await ctx.repo.get_user(user.user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    profile = await ctx.repo.get_profile(user.user_id)
    return MeResponse(user=_public(record), profile=ProfileModel(**profile))


@router.put("/me", response_model=UserPublic)
async def update_me(
    body: NicknameUpdate, request: Request, user: AuthUser = Depends(get_current_user)
):
    ctx = request.app.state.ctx
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="昵称不能为空")
    record = await ctx.repo.update_user(user.user_id, nickname=nickname)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return _public(record)


@router.put("/me/profile", response_model=ProfileModel)
async def update_profile(
    body: ProfileModel, request: Request, user: AuthUser = Depends(get_current_user)
):
    ctx = request.app.state.ctx
    profile = await ctx.repo.save_profile(user.user_id, body.model_dump())
    return ProfileModel(**profile)


@router.post("/me/avatar", response_model=UserPublic)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    ctx = request.app.state.ctx
    settings = ctx.settings
    max_bytes = settings.avatar_max_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"头像不能超过 {settings.avatar_max_mb} MB",
        )

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:  # pragma: no cover
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="缺少 Pillow，无法处理头像")

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不是有效的图片文件")
    if image.format not in _ALLOWED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="只支持 JPG / PNG")

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    size = settings.avatar_size
    image = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)

    avatar_dir = Path(settings.media_dir) / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    path = avatar_dir / f"{user.user_id}.webp"
    image.save(path, format="WEBP", quality=settings.avatar_quality, method=6)

    url = f"/media/avatars/{user.user_id}.webp?v={int(time.time())}"
    record = await ctx.repo.update_user(user.user_id, avatar=url)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return _public(record)


@router.delete("/me/avatar", response_model=OkResponse)
async def delete_avatar(request: Request, user: AuthUser = Depends(get_current_user)):
    ctx = request.app.state.ctx
    path = Path(ctx.settings.media_dir) / "avatars" / f"{user.user_id}.webp"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    await ctx.repo.update_user(user.user_id, avatar="")
    return OkResponse()
