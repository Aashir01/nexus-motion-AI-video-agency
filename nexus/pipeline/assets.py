"""Turning a provider response into a durable, addressable asset."""
from __future__ import annotations

from nexus.pipeline.context import ProductionContext
from nexus.providers.registry import get_provider
from nexus.routing.types import ModelResponse
from nexus.storage.base import StoredAsset
from nexus.util.errors import ProviderError

_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
    "video/mp4": "mp4", "video/webm": "webm",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg",
}


async def persist(ctx: ProductionContext, response: ModelResponse, rel_key: str) -> StoredAsset:
    """Download (if remote) and store, returning the stored asset.

    Providers split into two camps — some hand back bytes, some hand back a
    signed URL that expires. Everything is normalised into our own storage so
    a job's assets outlive the provider's retention window.
    """
    data = response.data
    if data is None:
        if not response.remote_url:
            raise ProviderError(
                "provider returned neither bytes nor a URL",
                provider=response.provider, model=response.model_id, retryable=False,
            )
        provider = get_provider(response.provider)
        download = getattr(provider, "download", None)
        if download is None:
            from nexus.providers.base import HttpProvider

            download = HttpProvider.download.__get__(provider)  # type: ignore[assignment]
        data = await download(response.remote_url)

    ext = _EXT.get(response.content_type, "bin")
    key = rel_key if "." in rel_key.rsplit("/", 1)[-1] else f"{rel_key}.{ext}"
    return await ctx.put_bytes(key, data, response.content_type or None)
