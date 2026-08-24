"""fal.ai adapter — the widest single-key surface for image, video and audio.

fal exposes an async queue: POST the payload, then poll the returned status URL.
Every model has its own payload shape, so the adapter carries a small amount of
per-family translation rather than pretending one schema fits all.
"""
from __future__ import annotations

from nexus.config import settings
from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import (
    AudioRequest,
    ImageRequest,
    Modality,
    ModelResponse,
    TTSRequest,
    Usage,
    VideoRequest,
)
from nexus.util.errors import ProviderError

_IMAGE_SIZE = {
    "16:9": "landscape_16_9", "9:16": "portrait_16_9", "1:1": "square_hd",
    "4:5": "portrait_4_3", "2.39:1": "landscape_16_9",
}


class FalProvider(HttpProvider):
    id = "fal"
    base_url = "https://queue.fal.run"
    timeout_s = 120.0

    def api_key(self) -> str:
        if not settings.fal_api_key:
            raise ProviderError("FAL_API_KEY is not configured", provider=self.id, retryable=False)
        return settings.fal_api_key

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self.api_key()}", "Content-Type": "application/json"}

    async def _submit_and_wait(self, model: str, payload: dict, *, max_wait_s: float) -> dict:
        submitted = await self.request("POST", f"/{model}", json=payload, model=model)
        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")
        if not status_url:
            return submitted  # some endpoints answer synchronously

        await self.poll_until_done(
            status_url,
            is_done=lambda p: p.get("status") == "COMPLETED",
            is_failed=lambda p: p.get("status") in ("FAILED", "CANCELLED", "ERROR"),
            interval_s=3.0, max_wait_s=max_wait_s, model=model,
        )
        return await self.request("GET", response_url, model=model)

    @staticmethod
    def _first_url(result: dict, *keys: str) -> str | None:
        for key in keys:
            node = result.get(key)
            if isinstance(node, dict) and node.get("url"):
                return node["url"]
            if isinstance(node, list) and node and isinstance(node[0], dict) and node[0].get("url"):
                return node[0]["url"]
            if isinstance(node, str) and node.startswith("http"):
                return node
        return None

    # ── image ────────────────────────────────────────────────────────────
    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        model = spec.provider_model
        payload: dict = {"prompt": req.prompt[:5000], "num_images": req.n}
        if req.negative_prompt and "flux-pro" not in model:
            payload["negative_prompt"] = req.negative_prompt
        if req.seed is not None:
            payload["seed"] = req.seed

        refs = ([req.edit_source_url] if req.edit_source_url else []) + list(req.reference_image_urls)
        refs = [r for r in refs if r][: spec.max_reference_images or 0]

        if "seedream" in model or "gemini" in model or "nano-banana" in model:
            payload["image_size"] = {"16:9": {"width": 1920, "height": 1080},
                                     "9:16": {"width": 1080, "height": 1920},
                                     "1:1": {"width": 1440, "height": 1440}}.get(
                req.aspect_ratio, {"width": 1920, "height": 1080})
            if refs:
                payload["image_urls"] = refs
        elif "kontext" in model:
            payload["aspect_ratio"] = req.aspect_ratio
            if refs:
                payload["image_url"] = refs[0]
        else:
            payload["image_size"] = _IMAGE_SIZE.get(req.aspect_ratio, "landscape_16_9")

        result = await self._submit_and_wait(model, payload, max_wait_s=420.0)
        url = self._first_url(result, "images", "image")
        if not url:
            raise ProviderError(f"no image in response: {str(result)[:300]}",
                                provider=self.id, model=model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                             remote_url=url, content_type="image/png", usage=Usage(images=req.n))

    # ── video ────────────────────────────────────────────────────────────
    async def generate_video(self, spec: ModelSpec, req: VideoRequest) -> ModelResponse:
        model = spec.provider_model
        duration = spec.clamp_duration(req.duration_s)
        payload: dict = {"prompt": req.prompt[:5000]}

        if req.negative_prompt and ("kling" in model or "wan" in model):
            payload["negative_prompt"] = req.negative_prompt
        if req.first_frame_url:
            payload["image_url"] = req.first_frame_url
        if req.last_frame_url and spec.supports_first_last_frame:
            payload["tail_image_url"] = req.last_frame_url
        if req.reference_image_urls and spec.supports_reference_images:
            payload["reference_image_urls"] = req.reference_image_urls[: spec.max_reference_images]

        if "kling" in model:
            payload["duration"] = str(round(duration))
            payload["aspect_ratio"] = req.aspect_ratio
            payload["cfg_scale"] = 0.5
        elif "veo" in model:
            payload["duration"] = f"{round(duration)}s"
            payload["aspect_ratio"] = req.aspect_ratio
            payload["resolution"] = req.resolution
            payload["generate_audio"] = bool(req.with_audio)
        elif "seedance" in model:
            payload["duration"] = round(duration)
            payload["resolution"] = req.resolution
            payload["aspect_ratio"] = req.aspect_ratio
            payload["generate_audio"] = bool(req.with_audio)
        else:  # wan / ltx and friends
            payload["duration"] = round(duration)
            payload["resolution"] = req.resolution
            payload["aspect_ratio"] = req.aspect_ratio
        if req.seed is not None:
            payload["seed"] = req.seed

        result = await self._submit_and_wait(
            model, payload, max_wait_s=float(settings.shot_generation_timeout_s)
        )
        url = self._first_url(result, "video", "videos")
        if not url:
            raise ProviderError(f"no video in response: {str(result)[:300]}",
                                provider=self.id, model=model)
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=Modality.VIDEO, remote_url=url,
            content_type="video/mp4", duration_s=duration, usage=Usage(seconds=duration),
        )

    # ── speech ───────────────────────────────────────────────────────────
    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        payload: dict = {"prompt": req.text, "text": req.text, "speed": req.speed}
        if req.voice_id:
            payload["voice"] = req.voice_id
        result = await self._submit_and_wait(spec.provider_model, payload, max_wait_s=300.0)
        url = self._first_url(result, "audio", "audio_url", "audio_file")
        if not url:
            raise ProviderError(f"no audio in response: {str(result)[:300]}",
                                provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.TTS,
                             remote_url=url, content_type="audio/mpeg",
                             usage=Usage(characters=len(req.text)))

    # ── music / sfx ──────────────────────────────────────────────────────
    async def generate_audio(self, spec: ModelSpec, req: AudioRequest) -> ModelResponse:
        payload = {"prompt": req.prompt[:2000], "seconds_total": round(req.duration_s),
                   "duration": round(req.duration_s)}
        result = await self._submit_and_wait(spec.provider_model, payload, max_wait_s=600.0)
        url = self._first_url(result, "audio", "audio_file", "audio_url")
        if not url:
            raise ProviderError(f"no audio in response: {str(result)[:300]}",
                                provider=self.id, model=spec.provider_model)
        modality = Modality.SFX if req.kind in ("sfx", "ambience") else Modality.MUSIC
        return ModelResponse(provider=self.id, model_id=spec.id, modality=modality,
                             remote_url=url, content_type="audio/mpeg",
                             duration_s=req.duration_s, usage=Usage(seconds=req.duration_s))


register(FalProvider.id, FalProvider)
