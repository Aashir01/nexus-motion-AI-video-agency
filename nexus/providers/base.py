"""Provider adapter contract plus a shared, retry-aware HTTP client."""
from __future__ import annotations

import asyncio
import random
from typing import Any

from nexus.config import settings
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import (
    AudioRequest,
    ImageRequest,
    LLMRequest,
    ModelResponse,
    TTSRequest,
    VideoRequest,
)
from nexus.util.errors import ProviderError
from nexus.util.logging import get_logger

log = get_logger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}


class Provider:
    """One adapter per upstream API. Only implement what the provider offers."""

    id: str = "base"

    async def generate_text(self, spec: ModelSpec, req: LLMRequest) -> ModelResponse:
        raise self._unsupported("text")

    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        raise self._unsupported("image")

    async def generate_video(self, spec: ModelSpec, req: VideoRequest) -> ModelResponse:
        raise self._unsupported("video")

    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        raise self._unsupported("tts")

    async def generate_audio(self, spec: ModelSpec, req: AudioRequest) -> ModelResponse:
        raise self._unsupported("audio")

    async def aclose(self) -> None:
        return None

    def _unsupported(self, modality: str) -> ProviderError:
        return ProviderError(
            f"provider {self.id!r} does not implement {modality} generation",
            provider=self.id, retryable=False,
        )


class HttpProvider(Provider):
    """Base for REST providers: one shared client, jittered exponential backoff,
    and long-poll support for async job APIs (fal, Replicate, Runway)."""

    base_url: str = ""
    timeout_s: float = 180.0

    def __init__(self) -> None:
        self._client = None
        self._lock = asyncio.Lock()

    async def client(self):
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    import httpx

                    self._client = httpx.AsyncClient(
                        base_url=self.base_url,
                        timeout=httpx.Timeout(self.timeout_s, connect=20.0),
                        follow_redirects=True,
                        limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def headers(self) -> dict[str, str]:
        return {}

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        model: str | None = None,
        expect_json: bool = True,
        max_retries: int | None = None,
        timeout_s: float | None = None,
    ):
        import httpx

        client = await self.client()
        merged = {**self.headers(), **(headers or {})}
        attempts = (max_retries if max_retries is not None else settings.http_max_retries) + 1
        last: Exception | None = None

        for attempt in range(attempts):
            try:
                resp = await client.request(
                    method, url, json=json, content=data, headers=merged,
                    timeout=timeout_s or self.timeout_s,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = ProviderError(f"transport failure: {exc}", provider=self.id, model=model)
            else:
                if resp.status_code < 400:
                    return resp.json() if expect_json else resp
                body = resp.text[:600]
                retryable = resp.status_code in _RETRYABLE_STATUS
                last = ProviderError(
                    f"HTTP {resp.status_code}: {body}",
                    provider=self.id, model=model, status=resp.status_code, retryable=retryable,
                )
                if not retryable:
                    raise last
                if resp.status_code == 429:
                    ra = resp.headers.get("retry-after")
                    if ra:
                        try:
                            await asyncio.sleep(min(float(ra), 60.0))
                            continue
                        except ValueError:
                            pass

            if attempt < attempts - 1:
                delay = min(2**attempt + random.uniform(0, 0.75), 30.0)
                log.info(
                    "provider_retry",
                    extra={"provider": self.id, "model": model, "attempt": attempt + 1, "delay_s": round(delay, 2)},
                )
                await asyncio.sleep(delay)

        raise last or ProviderError("request failed", provider=self.id, model=model)

    async def poll_until_done(
        self,
        status_url: str,
        *,
        is_done,
        is_failed=None,
        interval_s: float = 3.0,
        max_wait_s: float | None = None,
        headers: dict[str, str] | None = None,
        model: str | None = None,
    ) -> dict:
        """Poll an async job endpoint until `is_done(payload)` is truthy."""
        waited = 0.0
        limit = max_wait_s or settings.shot_generation_timeout_s
        delay = interval_s
        while waited < limit:
            payload = await self.request("GET", status_url, headers=headers, model=model)
            if is_failed and is_failed(payload):
                raise ProviderError(
                    f"generation job failed: {str(payload)[:400]}",
                    provider=self.id, model=model, retryable=True,
                )
            if is_done(payload):
                return payload
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 1.25, 15.0)
        raise ProviderError(
            f"generation timed out after {limit:.0f}s", provider=self.id, model=model, retryable=True
        )

    async def download(self, url: str) -> bytes:
        import httpx

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as dl:
            resp = await dl.get(url)
            if resp.status_code >= 400:
                raise ProviderError(
                    f"asset download failed HTTP {resp.status_code}",
                    provider=self.id, status=resp.status_code,
                )
            return resp.content
