from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from nexus.config import settings
from nexus.storage.base import Storage, StoredAsset, guess_content_type
from nexus.util.errors import NotFoundError


class LocalStorage(Storage):
    def __init__(self, root: str | Path | None = None, public_base: str | None = None):
        self.root = Path(root or settings.storage_local_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base = (public_base or f"{settings.public_base_url}/assets").rstrip("/")

    def _path(self, key: str) -> Path:
        safe = key.lstrip("/").replace("..", "_")
        target = (self.root / safe).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"path traversal blocked for key {key!r}")
        return target

    async def put_bytes(self, key, data, content_type=None) -> StoredAsset:
        def _write() -> int:
            p = self._path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return len(data)

        size = await asyncio.to_thread(_write)
        return StoredAsset(key, self.url_for(key), size, content_type or guess_content_type(key))

    async def put_file(self, key, path, content_type=None) -> StoredAsset:
        def _copy() -> int:
            src = Path(path)
            dst = self._path(key)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != dst:
                shutil.copyfile(src, dst)
            return dst.stat().st_size

        size = await asyncio.to_thread(_copy)
        return StoredAsset(key, self.url_for(key), size, content_type or guess_content_type(key))

    async def get_bytes(self, key) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise NotFoundError(f"asset {key} not found")
        return await asyncio.to_thread(p.read_bytes)

    async def exists(self, key) -> bool:
        return await asyncio.to_thread(self._path(key).exists)

    async def delete(self, key) -> None:
        p = self._path(key)
        if p.exists():
            await asyncio.to_thread(p.unlink)

    def url_for(self, key) -> str:
        return f"{self.public_base}/{key.lstrip('/')}"

    async def local_path(self, key) -> Path:
        p = self._path(key)
        if not p.exists():
            raise NotFoundError(f"asset {key} not found")
        return p
