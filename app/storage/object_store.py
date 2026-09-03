"""对象存储：生成图片、表情包等二进制资源的持久化。

占位实现：本地目录落盘（LOCAL_OBJECT_DIR 配置后可用）；
生产可替换为 S3 / OSS / MinIO 客户端（保留 upload_bytes 接口即可）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StoredObject:
    key: str
    url: str


class ObjectStore:
    def __init__(self, local_dir: str | None = None, public_base: str | None = None) -> None:
        self._dir = Path(local_dir) if local_dir else None
        self._base = public_base or ""

    async def upload_bytes(self, data: bytes, content_type: str = "application/octet-stream", key: str | None = None) -> StoredObject | None:
        if self._dir is None:
            logger.warning("对象存储未配置，跳过持久化")
            return None
        key = key or uuid.uuid4().hex
        ext = _ext_for(content_type)
        path = self._dir / f"{key}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(key=f"{key}{ext}", url=f"{self._base}/{key}{ext}")


def _ext_for(content_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".bin")
