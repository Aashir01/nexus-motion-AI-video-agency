"""Asset storage abstraction: local filesystem in dev, S3/R2 in production."""
from __future__ import annotations

import abc
import mimetypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class StoredAsset:
    key: str
    url: str
    size_bytes: int
    content_type: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "url": self.url,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
        }


def guess_content_type(key: str) -> str:
    ctype, _ = mimetypes.guess_type(key)
    return ctype or "application/octet-stream"


class Storage(abc.ABC):
    """Every asset the pipeline produces goes through this interface."""

    @abc.abstractmethod
    async def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> StoredAsset: ...

    @abc.abstractmethod
    async def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> StoredAsset: ...

    @abc.abstractmethod
    async def get_bytes(self, key: str) -> bytes: ...

    @abc.abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    async def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def url_for(self, key: str) -> str: ...

    @abc.abstractmethod
    async def local_path(self, key: str) -> Path:
        """Materialise the object on local disk (ffmpeg needs real files)."""
