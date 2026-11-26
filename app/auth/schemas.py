"""鉴权与用户资料请求/响应模型。"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=4, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=6, max_length=128)
    nickname: str = Field(default="", max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    device_id: str = Field(default="", max_length=128)
    platform: str = Field(default="web", max_length=16)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=8, max_length=200)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(default="", max_length=200)
    all_devices: bool = False


class UserPublic(BaseModel):
    user_id: int
    username: str
    nickname: str
    avatar: str = ""
    is_creator: bool = False
    status: int = 1


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


class ProfileModel(BaseModel):
    gender: int = Field(default=0, ge=0, le=2)
    birthday: date | None = None
    bio: str = Field(default="", max_length=255)
    tags: list[str] = Field(default_factory=list, max_length=12)
    traits: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class MeResponse(BaseModel):
    user: UserPublic
    profile: ProfileModel


class NicknameUpdate(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)


class OkResponse(BaseModel):
    ok: bool = True
