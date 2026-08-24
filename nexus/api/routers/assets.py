"""Serves generated assets in local-storage deployments.

With `STORAGE_BACKEND=s3` these URLs point straight at the bucket or CDN and
this router is never hit; it exists so a single-container deployment works with
no object store at all.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from nexus.config import settings
from nexus.storage import get_storage
from nexus.storage.base import guess_content_type
from nexus.util.errors import NotFoundError

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/{key:path}")
async def get_asset(key: str):
    storage = get_storage()
    if settings.storage_backend == "s3":
        from nexus.storage.s3 import S3Storage

        assert isinstance(storage, S3Storage)
        return Response(status_code=307, headers={"Location": storage.presigned_url(key)})

    try:
        path = await storage.local_path(key)
    except Exception as exc:
        raise NotFoundError(f"asset {key} not found") from exc

    return FileResponse(
        path, media_type=guess_content_type(key),
        headers={"Cache-Control": "public, max-age=3600", "Accept-Ranges": "bytes"},
    )
