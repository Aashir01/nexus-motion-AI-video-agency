"""OpenAI-compatible chat completions.

One adapter class covers every provider that speaks the OpenAI wire format:
OpenAI itself, OpenRouter, Groq, DeepSeek, Together, Fireworks, Mistral, xAI,
Cerebras and a local Ollama. Only the base URL, key and a couple of quirks vary.

OpenAI also provides images and TTS, so its subclass implements those too.
"""
from __future__ import annotations

import base64

from nexus.config import settings
from nexus.providers._json import extract_json, schema_instructions
from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import (
    ImageRequest,
    LLMRequest,
    Modality,
    ModelResponse,
    TTSRequest,
    Usage,
)
from nexus.util.errors import ProviderError


class OpenAICompatProvider(HttpProvider):
    id = "openai_compat"
    base_url = "https://api.openai.com/v1"
    credential_field = "openai_api_key"
    supports_response_format_schema = True
    extra_headers: dict[str, str] = {}

    def api_key(self) -> str:
        key = getattr(settings, self.credential_field, None)
        if not key:
            raise ProviderError(
                f"{self.credential_field.upper()} is not configured",
                provider=self.id, retryable=False,
            )
        return key

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key()}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

    def _build_messages(self, req: LLMRequest) -> list[dict]:
        content: list[dict] | str
        if req.image_urls:
            content = [{"type": "text", "text": req.prompt}]
            for url in req.image_urls:
                content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            content = req.prompt

        system = req.system
        if req.json_schema and not self.supports_response_format_schema:
            system = f"{system}\n\n{schema_instructions(req.json_schema)}"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return messages

    async def generate_text(self, spec: ModelSpec, req: LLMRequest) -> ModelResponse:
        body: dict = {
            "model": spec.provider_model,
            "messages": self._build_messages(req),
            "max_tokens": min(req.max_tokens, spec.max_output_tokens or req.max_tokens),
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.stop:
            body["stop"] = req.stop
        if req.json_schema:
            if self.supports_response_format_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": req.label.replace("-", "_")[:60] or "response",
                        "schema": req.json_schema,
                        "strict": False,
                    },
                }
            else:
                body["response_format"] = {"type": "json_object"}

        payload = await self.request(
            "POST", "/chat/completions", json=body, model=spec.provider_model,
            timeout_s=settings.llm_timeout_s,
        )

        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError("response contained no choices", provider=self.id, model=spec.provider_model)
        text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise ProviderError("empty completion", provider=self.id, model=spec.provider_model)

        usage_raw = payload.get("usage") or {}
        usage = Usage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
            cached_input_tokens=(usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
        parsed = extract_json(text) if req.json_schema else None
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=req.modality,
            text=text, parsed=parsed, usage=usage, raw={"finish_reason": choices[0].get("finish_reason")},
        )


class OpenAIProvider(OpenAICompatProvider):
    id = "openai"
    base_url = "https://api.openai.com/v1"
    credential_field = "openai_api_key"

    _SIZES = {"16:9": "1536x1024", "9:16": "1024x1536", "1:1": "1024x1024",
              "4:5": "1024x1536", "2.39:1": "1536x1024"}

    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        prompt = req.prompt
        if req.negative_prompt:
            prompt += f"\n\nAvoid: {req.negative_prompt}"
        body = {
            "model": spec.provider_model,
            "prompt": prompt[:4000],
            "size": self._SIZES.get(req.aspect_ratio, "1536x1024"),
            "n": req.n,
        }
        payload = await self.request("POST", "/images/generations", json=body,
                                     model=spec.provider_model, timeout_s=240.0)
        items = payload.get("data") or []
        if not items:
            raise ProviderError("no image returned", provider=self.id, model=spec.provider_model)
        first = items[0]
        if first.get("b64_json"):
            data = base64.b64decode(first["b64_json"])
            return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                                 data=data, content_type="image/png",
                                 usage=Usage(images=len(items)))
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
                             remote_url=first.get("url"), content_type="image/png",
                             usage=Usage(images=len(items)))

    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        body = {
            "model": spec.provider_model,
            "input": req.text,
            "voice": req.voice_id or "alloy",
            "response_format": "mp3",
            "speed": max(0.25, min(req.speed, 4.0)),
        }
        instruction = " ".join(x for x in (req.style, req.emotion) if x)
        if instruction:
            body["instructions"] = instruction
        resp = await self.request("POST", "/audio/speech", json=body,
                                  model=spec.provider_model, expect_json=False, timeout_s=180.0)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.TTS,
                             data=resp.content, content_type="audio/mpeg",
                             usage=Usage(characters=len(req.text)))


class OpenRouterProvider(OpenAICompatProvider):
    id = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    credential_field = "openrouter_api_key"
    supports_response_format_schema = True

    @property
    def extra_headers(self) -> dict[str, str]:  # type: ignore[override]
        return {
            "HTTP-Referer": settings.public_base_url,
            "X-Title": settings.app_name,
        }


class GroqProvider(OpenAICompatProvider):
    id = "groq"
    base_url = "https://api.groq.com/openai/v1"
    credential_field = "groq_api_key"
    supports_response_format_schema = False


class DeepSeekProvider(OpenAICompatProvider):
    id = "deepseek"
    base_url = "https://api.deepseek.com/v1"
    credential_field = "deepseek_api_key"
    supports_response_format_schema = False


class TogetherProvider(OpenAICompatProvider):
    id = "together"
    base_url = "https://api.together.xyz/v1"
    credential_field = "together_api_key"
    supports_response_format_schema = False


class FireworksProvider(OpenAICompatProvider):
    id = "fireworks"
    base_url = "https://api.fireworks.ai/inference/v1"
    credential_field = "fireworks_api_key"
    supports_response_format_schema = False


class MistralProvider(OpenAICompatProvider):
    id = "mistral"
    base_url = "https://api.mistral.ai/v1"
    credential_field = "mistral_api_key"
    supports_response_format_schema = False


class XAIProvider(OpenAICompatProvider):
    id = "xai"
    base_url = "https://api.x.ai/v1"
    credential_field = "xai_api_key"


class CerebrasProvider(OpenAICompatProvider):
    id = "cerebras"
    base_url = "https://api.cerebras.ai/v1"
    credential_field = "cerebras_api_key"
    supports_response_format_schema = False


class OllamaProvider(OpenAICompatProvider):
    id = "ollama"
    credential_field = "ollama_base_url"
    supports_response_format_schema = False

    @property
    def base_url(self) -> str:  # type: ignore[override]
        return (settings.ollama_base_url or "http://localhost:11434") + "/v1"

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}


for _cls in (
    OpenAIProvider, OpenRouterProvider, GroqProvider, DeepSeekProvider,
    TogetherProvider, FireworksProvider, MistralProvider, XAIProvider,
    CerebrasProvider, OllamaProvider,
):
    register(_cls.id, _cls)
