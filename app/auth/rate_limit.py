"""认证限流与防爆破：按 IP 限流 + 账号连续失败锁定。

设计：
- 每个来源 IP 每分钟的认证请求（注册+登录）有统一上限，防止刷号与批量撞库；
- 每个账号连续登录失败达到阈值后锁定一段时间，防止单账号暴力破解；
- IP 取真实来源，只有部署在可信反向代理后（Settings.trust_proxy_headers=True）
  才信任 X-Forwarded-For，否则用直连 socket 地址，避免伪造头绕过限流。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.graph.context import GraphContext


def client_ip(request, trust_proxy: bool) -> str:
    """取真实来源 IP。反代场景需开启 trust_proxy_headers 才信任 X-Forwarded-For 首项。"""
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_auth_rate_limit(ctx: GraphContext, ip: str) -> None:
    """每 IP 每分钟认证请求上限（注册+登录共用），超限抛 429。"""
    count = await ctx.cache.incr(f"auth:rl:{ip}", ttl=60)
    if count > ctx.settings.auth_rate_limit_per_min:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


async def is_login_locked(ctx: GraphContext, username: str) -> bool:
    return await ctx.cache.get(f"auth:lock:{username}") is not None


async def record_login_failure(ctx: GraphContext, username: str) -> None:
    """记录一次登录失败；达到阈值则锁定账号 login_lockout_sec 秒。"""
    fails = await ctx.cache.incr(f"auth:fail:{username}", ttl=ctx.settings.login_lockout_sec)
    if fails >= ctx.settings.login_max_failures:
        await ctx.cache.set(f"auth:lock:{username}", "1", ttl=ctx.settings.login_lockout_sec)


async def clear_login_failures(ctx: GraphContext, username: str) -> None:
    """登录成功时清空失败计数与锁定。"""
    await ctx.cache.delete(f"auth:fail:{username}")
    await ctx.cache.delete(f"auth:lock:{username}")
