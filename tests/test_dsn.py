"""本机 PG 的 SSL 处理：不允许因为 home 不可读而在启动时崩。"""
from app.core.checkpointing import _libpq_url, _with_local_sslmode
from app.storage.postgres import is_loopback_url


def test_libpq_url_strips_asyncpg_driver():
    assert _libpq_url("postgresql+asyncpg://u:p@127.0.0.1:5432/db").startswith("postgresql://")


def test_local_dsn_gets_sslmode_disable():
    out = _with_local_sslmode("postgresql://u:p@127.0.0.1:5432/db")
    assert "sslmode=disable" in out


def test_local_dsn_keeps_explicit_sslmode():
    out = _with_local_sslmode("postgresql://u:p@127.0.0.1:5432/db?sslmode=require")
    assert "sslmode=require" in out and "sslmode=disable" not in out


def test_remote_dsn_untouched():
    dsn = "postgresql://u:p@db.internal:5432/db"
    assert _with_local_sslmode(dsn) == dsn


def test_is_loopback_url():
    assert is_loopback_url("postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    assert is_loopback_url("postgresql+asyncpg://u:p@localhost:5432/db")
    assert not is_loopback_url("postgresql+asyncpg://u:p@10.0.0.5:5432/db")
