"""密码哈希（argon2id，兼容旧 PBKDF2 并惰性升级）、JWT、refresh token。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

try:  # 生产依赖；测试环境若缺则退回 PBKDF2（仅兼容）
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    _HASHER = PasswordHasher(
        time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16
    )
    _HAS_ARGON = True
except Exception:  # noqa: BLE001
    _HASHER = None
    _HAS_ARGON = False

_PBKDF2_ITERATIONS = 120_000
_ARGON_PREFIX = "$argon2id$"


# ---------------- 密码 ----------------


def hash_password(password: str) -> str:
    """argon2id；未安装 argon2-cffi 时退回 PBKDF2-SHA256（仅兼容）。"""
    if _HAS_ARGON and _HASHER is not None:
        return _HASHER.hash(password)
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    if not encoded:
        return False
    if encoded.startswith(_ARGON_PREFIX):
        if not _HAS_ARGON or _HASHER is None:
            return False
        try:
            return _HASHER.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
        except Exception:  # noqa: BLE001
            return False
    # 旧 PBKDF2：校验通过后由调用方重哈希
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


def needs_rehash(encoded: str) -> bool:
    """旧 PBKDF2 或参数过弱的 argon2 需要重算。"""
    if not encoded:
        return True
    if not encoded.startswith(_ARGON_PREFIX):
        return True
    if not _HAS_ARGON or _HASHER is None:
        return False
    try:
        return _HASHER.check_needs_rehash(encoded)
    except Exception:  # noqa: BLE001
        return True


# ---------------- refresh token ----------------


def create_refresh_token() -> str:
    """返回给客户端的明文 refresh token（库里只存 sha256）。"""
    return "rt_" + secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------- JWT ----------------


def create_access_token(
    user_id: int | str,
    username: str,
    secret: str,
    algorithm: str = "HS256",
    expires_minutes: int = 30,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, secret: str, algorithm: str = "HS256") -> dict:
    # 无效/过期会抛 jwt.PyJWTError，由调用方捕获
    return jwt.decode(token, secret, algorithms=[algorithm])
