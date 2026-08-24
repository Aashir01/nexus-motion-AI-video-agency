"""Hugging Face Inference API — free-tier image and video."""
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

_DIMS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
         "4:5": (896, 1120), "2.39:1": (1344, 562)}


class HuggingFaceProvider(HttpProvider):
    id = "huggingface"
    base_url = "https://api-inference.huggingface.co"
    timeout_s = 300.0

    def headers(self) -> dict[str, str]:
        if not settings.huggingface_api_key:
            raise ProviderError("HUGGINGFACE_API_KEY is not configured",
                                provider=self.id, retryable=False)
        return {
            "Authorization": f"Bearer {settings.huggingface_api_key}",
            "Content-Type": "application/json",
        }

    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        width, height = _DIMS.get(req.aspect_ratio, (1280, 720))
        body = {
            "inputs": req.prompt[:2000],
            "parameters": {"width": width, "height": height,
                           "negative_prompt": req.negative_prompt or None},
            "options": {"wait_for_model": True},
        }
        resp = await self.request("POST", f"/models/{spec.provider_model}", json=body,
                                  model=spec.provider_model, expect_json=False)
        if not resp.content or resp.headers.get("content-type", "").startswith("application/json"):
            raise ProviderError(f"unexpected response: {resp.text[:200]}",
                                provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                             data=resp.content, content_type="image/png", usage=Usage(images=1))

    async def generate_video(self, spec: ModelSpec, req: VideoRequest) -> ModelResponse:
        duration = spec.clamp_duration(req.duration_s)
        body = {
            "inputs": req.prompt[:2000],
            "parameters": {"num_frames": int(duration * 24)},
            "options": {"wait_for_model": True},
        }
        resp = await self.request("POST", f"/models/{spec.provider_model}", json=body,
                                  model=spec.provider_model, expect_json=False,
                                  timeout_s=float(settings.shot_generation_timeout_s))
        if not resp.content:
            raise ProviderError("empty video response", provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.VIDEO,
                             data=resp.content, content_type="video/mp4", duration_s=duration,
                             usage=Usage(seconds=duration))


register(HuggingFaceProvider.id, HuggingFaceProvider)
