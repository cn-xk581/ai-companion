"""认证限流与防爆破。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth.rate_limit import (
    clear_login_failures,
    client_ip,
    enforce_auth_rate_limit,
    is_login_locked,
    record_login_failure,
)
from tests.conftest import make_ctx


class _FakeRequest:
    def __init__(self, host: str = "1.2.3.4", xff: str | None = None):
        self.client = type("Client", (), {"host": host})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_client_ip_no_proxy_trust_ignores_spoofed_xff():
    # 默认不信任反代头：伪造 X-Forwarded-For 不能绕过限流
    req = _FakeRequest(host="1.2.3.4", xff="9.9.9.9")
    assert client_ip(req, trust_proxy=False) == "1.2.3.4"


def test_client_ip_trust_proxy_uses_xff_first_hop():
    req = _FakeRequest(host="1.2.3.4", xff="9.9.9.9, 8.8.8.8")
    assert client_ip(req, trust_proxy=True) == "9.9.9.9"


async def test_auth_rate_limit_rejects_after_threshold():
    ctx = await make_ctx()
    ctx.settings.auth_rate_limit_per_min = 3
    for _ in range(3):
        await enforce_auth_rate_limit(ctx, "1.2.3.4")  # 前 3 次通过
    with pytest.raises(HTTPException) as exc:
        await enforce_auth_rate_limit(ctx, "1.2.3.4")
    assert exc.value.status_code == 429


async def test_login_lockout_after_failures_and_clear_on_success():
    ctx = await make_ctx()
    ctx.settings.login_max_failures = 3
    ctx.settings.login_lockout_sec = 900

    assert await is_login_locked(ctx, "alice") is False
    await record_login_failure(ctx, "alice")
    await record_login_failure(ctx, "alice")
    assert await is_login_locked(ctx, "alice") is False  # 未达阈值

    await record_login_failure(ctx, "alice")
    assert await is_login_locked(ctx, "alice") is True  # 达到阈值锁定

    # 登录成功后清除失败计数与锁定
    await clear_login_failures(ctx, "alice")
    assert await is_login_locked(ctx, "alice") is False
