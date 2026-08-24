from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from nexus.config import settings
from nexus.storage.base import Storage, StoredAsset, guess_content_type
from nexus.util.errors import ConfigurationError, NotFoundError


class S3Storage(Storage):
    """Works against AWS S3, Cloudflare R2, Backblaze B2 and MinIO."""

    def __init__(self) -> None:
        try:
            import boto3  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise ConfigurationError("boto3 is required for STORAGE_BACKEND=s3") from exc
        self.bucket = settings.s3_bucket
        self._cache_dir = Path(tempfile.gettempdir()) / "nexus-s3-cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                region_name=settings.s3_region,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
            )
        return self._client

    async def put_bytes(self, key, data, content_type=None) -> StoredAsset:
        ctype = content_type or guess_content_type(key)
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket, Key=key, Body=data, ContentType=ctype,
        )
        return StoredAsset(key, self.url_for(key), len(data), ctype)

    async def put_file(self, key, path, content_type=None) -> StoredAsset:
        ctype = content_type or guess_content_type(key)
        src = Path(path)
        await asyncio.to_thread(
            self.client.upload_file,
            str(src), self.bucket, key, {"ContentType": ctype},
        )
        return StoredAsset(key, self.url_for(key), src.stat().st_size, ctype)

    async def get_bytes(self, key) -> bytes:
        def _get() -> bytes:
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except self.client.exceptions.NoSuchKey as exc:
                raise NotFoundError(f"asset {key} not found") from exc

        return await asyncio.to_thread(_get)

    async def exists(self, key) -> bool:
        def _head() -> bool:
            try:
                self.client.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)

    async def delete(self, key) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    def url_for(self, key) -> str:
        if settings.s3_public_base_url:
            return f"{settings.s3_public_base_url.rstrip('/')}/{key.lstrip('/')}"
        if settings.s3_endpoint_url:
            return f"{settings.s3_endpoint_url.rstrip('/')}/{self.bucket}/{key.lstrip('/')}"
        return f"https://{self.bucket}.s3.{settings.s3_region}.amazonaws.com/{key.lstrip('/')}"

    def presigned_url(self, key: str, expires_s: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
        )

    async def local_path(self, key) -> Path:
        dest = self._cache_dir / key.replace("/", "__")
        if not dest.exists():
            data = await self.get_bytes(key)
            await asyncio.to_thread(dest.write_bytes, data)
        return dest
