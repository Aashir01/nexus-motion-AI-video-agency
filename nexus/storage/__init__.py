from __future__ import annotations

from functools import lru_cache

from nexus.config import settings
from nexus.storage.base import Storage, StoredAsset


@lru_cache
def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        from nexus.storage.s3 import S3Storage

        return S3Storage()
    from nexus.storage.local import LocalStorage

    return LocalStorage()


def asset_key(*parts: str) -> str:
    return "/".join(str(p).strip("/") for p in parts if p is not None)


__all__ = ["Storage", "StoredAsset", "asset_key", "get_storage"]
