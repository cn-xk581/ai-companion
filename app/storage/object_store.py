"""对象存储：本地 / S3 兼容（阿里云 OSS、腾讯云 COS、MinIO）。"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class ObjectStorage(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """写入并返回可访问 URL。"""

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class LocalStorage(ObjectStorage):
    def __init__(self, root: str | Path, public_prefix: str = "/media") -> None:
        self._root = Path(root)
        self._prefix = public_prefix.rstrip("/")

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        safe = key.lstrip("/").replace("..", "")
        return self._root / safe

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return f"{self._prefix}/{key.lstrip('/')}"

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)


class S3Storage(ObjectStorage):
    """S3 兼容对象存储（需要 pip install 'companion-agent[s3]'）。"""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str = "",
        access_key_id: str = "",
        access_key_secret: str = "",
        public_base_url: str = "",
    ) -> None:
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("STORAGE_BACKEND=s3 需要安装 boto3：pip install 'companion-agent[s3]'") from exc
        if not endpoint or not bucket:
            raise RuntimeError("S3_ENDPOINT / S3_BUCKET 未配置")
        self._bucket = bucket
        self._public = (public_base_url or f"{endpoint.rstrip('/')}/{bucket}").rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint if endpoint.startswith("http") else f"https://{endpoint}",
            region_name=region or None,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=access_key_secret or None,
        )

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"{self._public}/{key}"

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)


def create_storage(settings) -> ObjectStorage:
    backend = (settings.storage_backend or "local").lower()
    if backend in ("s3", "oss", "cos", "minio"):
        logger.info("[storage] 使用 S3 兼容对象存储 bucket=%s", settings.s3_bucket)
        return S3Storage(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            access_key_secret=settings.s3_access_key_secret,
            public_base_url=settings.s3_public_base_url,
        )
    if backend != "local":
        raise RuntimeError(f"未知 STORAGE_BACKEND：{backend}")
    logger.info("[storage] 使用本地媒体目录 %s", settings.media_dir)
    return LocalStorage(settings.media_dir, settings.storage_public_prefix)


__all__ = ["ObjectStorage", "LocalStorage", "S3Storage", "create_storage"]
