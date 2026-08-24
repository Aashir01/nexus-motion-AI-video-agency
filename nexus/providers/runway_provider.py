"""Runway Gen-4 adapter (image-to-video with reference conditioning)."""
from __future__ import annotations

from nexus.config import settings
from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import Modality, ModelResponse, Usage, VideoRequest
from nexus.util.errors import ProviderError

_RATIO = {"16:9": "1280:720", "9:16": "720:1280", "1:1": "960:960"}


class RunwayProvider(HttpProvider):
    id = "runway"
    base_url = "https://api.dev.runwayml.com/v1"

    def api_key(self) -> str:
        if not settings.runway_api_key:
            raise ProviderError("RUNWAY_API_KEY is not configured", provider=self.id, retryable=False)
        return settings.runway_api_key

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key()}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }

    async def generate_video(self, spec: ModelSpec, req: VideoRequest) -> ModelResponse:
        if not req.first_frame_url:
            raise ProviderError("Runway image-to-video requires a first frame",
                                provider=self.id, model=spec.provider_model, retryable=False)
        duration = round(spec.clamp_duration(req.duration_s))
        payload = {
            "model": spec.provider_model,
            "promptImage": req.first_frame_url,
            "promptText": req.prompt[:1000],
            "duration": duration,
            "ratio": _RATIO.get(req.aspect_ratio, "1280:720"),
        }
        if req.seed is not None:
            payload["seed"] = req.seed

        created = await self.request("POST", "/image_to_video", json=payload,
                                     model=spec.provider_model)
        task_id = created.get("id")
        if not task_id:
            raise ProviderError(f"no task id returned: {str(created)[:200]}",
                                provider=self.id, model=spec.provider_model)
        result = await self.poll_until_done(
            f"/tasks/{task_id}",
            is_done=lambda p: p.get("status") == "SUCCEEDED",
            is_failed=lambda p: p.get("status") in ("FAILED", "CANCELLED"),
            interval_s=5.0, max_wait_s=float(settings.shot_generation_timeout_s),
            model=spec.provider_model,
        )
        output = result.get("output") or []
        url = output[0] if output else None
        if not url:
            raise ProviderError("task succeeded but returned no output",
                                provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.VIDEO,
                             remote_url=url, content_type="video/mp4", duration_s=duration,
                             usage=Usage(seconds=duration))


register(RunwayProvider.id, RunwayProvider)
