"""Replicate adapter — prediction API with polling."""
from __future__ import annotations

from nexus.config import settings
from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import (
    ImageRequest,
    Modality,
    ModelResponse,
    Usage,
    VideoRequest,
)
from nexus.util.errors import ProviderError


class ReplicateProvider(HttpProvider):
    id = "replicate"
    base_url = "https://api.replicate.com/v1"

    def api_key(self) -> str:
        if not settings.replicate_api_token:
            raise ProviderError("REPLICATE_API_TOKEN is not configured",
                                provider=self.id, retryable=False)
        return settings.replicate_api_token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key()}", "Content-Type": "application/json"}

    async def _run(self, model: str, payload: dict, *, max_wait_s: float) -> dict:
        created = await self.request(
            "POST", f"/models/{model}/predictions", json={"input": payload}, model=model
        )
        get_url = ((created.get("urls") or {}).get("get")) or f"/predictions/{created.get('id')}"
        if created.get("status") == "succeeded":
            return created
        return await self.poll_until_done(
            get_url,
            is_done=lambda p: p.get("status") == "succeeded",
            is_failed=lambda p: p.get("status") in ("failed", "canceled"),
            interval_s=3.0, max_wait_s=max_wait_s, model=model,
        )

    @staticmethod
    def _output_url(result: dict) -> str | None:
        out = result.get("output")
        if isinstance(out, str):
            return out
        if isinstance(out, list) and out:
            return out[0] if isinstance(out[0], str) else None
        if isinstance(out, dict):
            for key in ("video", "image", "url", "output"):
                if isinstance(out.get(key), str):
                    return out[key]
        return None

    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        payload: dict = {"prompt": req.prompt[:5000], "aspect_ratio": req.aspect_ratio,
                         "output_format": "png"}
        source = req.edit_source_url or (req.reference_image_urls[0] if req.reference_image_urls else None)
        if source:
            payload["input_image"] = source
        if req.seed is not None:
            payload["seed"] = req.seed
        result = await self._run(spec.provider_model, payload, max_wait_s=420.0)
        url = self._output_url(result)
        if not url:
            raise ProviderError(f"no image output: {str(result)[:300]}",
                                provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                             remote_url=url, content_type="image/png", usage=Usage(images=1))

    async def generate_video(self, spec: ModelSpec, req: VideoRequest) -> ModelResponse:
        duration = spec.clamp_duration(req.duration_s)
        payload: dict = {"prompt": req.prompt[:5000], "duration": round(duration),
                         "aspect_ratio": req.aspect_ratio}
        if req.first_frame_url:
            payload["start_image"] = req.first_frame_url
            payload["image"] = req.first_frame_url
        if req.negative_prompt:
            payload["negative_prompt"] = req.negative_prompt
        result = await self._run(spec.provider_model, payload,
                                 max_wait_s=float(settings.shot_generation_timeout_s))
        url = self._output_url(result)
        if not url:
            raise ProviderError(f"no video output: {str(result)[:300]}",
                                provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.VIDEO,
                             remote_url=url, content_type="video/mp4", duration_s=duration,
                             usage=Usage(seconds=duration))


register(ReplicateProvider.id, ReplicateProvider)
