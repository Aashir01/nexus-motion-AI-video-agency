"""Pollinations — keyless free image endpoint. Draft quality, no SLA."""
from __future__ import annotations

import urllib.parse

from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import ImageRequest, Modality, ModelResponse, Usage
from nexus.util.errors import ProviderError

_DIMS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
         "4:5": (896, 1120), "2.39:1": (1344, 562)}


class PollinationsProvider(HttpProvider):
    id = "pollinations"
    base_url = "https://image.pollinations.ai"
    timeout_s = 180.0

    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        width, height = _DIMS.get(req.aspect_ratio, (1280, 720))
        prompt = urllib.parse.quote(req.prompt[:1500])
        params = f"width={width}&height={height}&model={spec.provider_model}&nologo=true"
        if req.seed is not None:
            params += f"&seed={req.seed}"
        resp = await self.request("GET", f"/prompt/{prompt}?{params}",
                                  model=spec.provider_model, expect_json=False, max_retries=2)
        if not resp.content or len(resp.content) < 1024:
            raise ProviderError("pollinations returned no usable image",
                                provider=self.id, model=spec.provider_model)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                             data=resp.content, content_type="image/jpeg", usage=Usage(images=1))


register(PollinationsProvider.id, PollinationsProvider)
