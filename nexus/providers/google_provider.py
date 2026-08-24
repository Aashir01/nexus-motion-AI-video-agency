"""Google Gemini adapter — text, vision and reference-conditioned image editing."""
from __future__ import annotations

import base64

from nexus.config import settings
from nexus.providers._json import extract_json
from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import ImageRequest, LLMRequest, Modality, ModelResponse, Usage
from nexus.util.errors import ProviderError


class GoogleProvider(HttpProvider):
    id = "google"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def api_key(self) -> str:
        if not settings.google_api_key:
            raise ProviderError("GOOGLE_API_KEY is not configured", provider=self.id, retryable=False)
        return settings.google_api_key

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key()}

    async def _inline_image(self, url: str) -> dict:
        if url.startswith("data:"):
            header, _, payload = url.partition(",")
            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
            return {"inline_data": {"mime_type": mime, "data": payload}}
        data = await self.download(url)
        mime = "image/png" if url.lower().endswith(".png") else "image/jpeg"
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}

    async def generate_text(self, spec: ModelSpec, req: LLMRequest) -> ModelResponse:
        parts: list[dict] = [{"text": req.prompt}]
        for url in req.image_urls:
            parts.append(await self._inline_image(url))

        generation_config: dict = {
            "maxOutputTokens": min(req.max_tokens, spec.max_output_tokens or req.max_tokens),
        }
        if req.temperature is not None:
            generation_config["temperature"] = req.temperature
        if req.json_schema:
            generation_config["responseMimeType"] = "application/json"

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}

        payload = await self.request(
            "POST", f"/models/{spec.provider_model}:generateContent", json=body,
            model=spec.provider_model, timeout_s=settings.llm_timeout_s,
        )

        candidates = payload.get("candidates") or []
        if not candidates:
            reason = (payload.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(
                f"no candidates returned{f' (blocked: {reason})' if reason else ''}",
                provider=self.id, model=spec.provider_model, retryable=bool(reason),
            )
        text = "".join(
            p.get("text", "") for p in (candidates[0].get("content") or {}).get("parts", [])
        )
        if not text.strip():
            raise ProviderError("empty completion", provider=self.id, model=spec.provider_model)

        um = payload.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=um.get("promptTokenCount", 0),
            output_tokens=um.get("candidatesTokenCount", 0),
            cached_input_tokens=um.get("cachedContentTokenCount", 0),
        )
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=req.modality, text=text,
            parsed=extract_json(text) if req.json_schema else None, usage=usage,
        )

    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        prompt = req.prompt
        if req.negative_prompt:
            prompt += f"\n\nStrictly avoid: {req.negative_prompt}"
        parts: list[dict] = [{"text": prompt}]
        sources = ([req.edit_source_url] if req.edit_source_url else []) + list(req.reference_image_urls)
        for url in sources[: spec.max_reference_images or 6]:
            parts.append(await self._inline_image(url))

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": req.aspect_ratio},
            },
        }
        payload = await self.request(
            "POST", f"/models/{spec.provider_model}:generateContent", json=body,
            model=spec.provider_model, timeout_s=300.0,
        )
        for candidate in payload.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts", []):
                blob = part.get("inlineData") or part.get("inline_data")
                if blob and blob.get("data"):
                    return ModelResponse(
                        provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                        data=base64.b64decode(blob["data"]),
                        content_type=blob.get("mimeType") or blob.get("mime_type") or "image/png",
                        usage=Usage(images=1),
                    )
        raise ProviderError("response contained no image data", provider=self.id,
                            model=spec.provider_model)


register(GoogleProvider.id, GoogleProvider)
