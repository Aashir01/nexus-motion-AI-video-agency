"""Anthropic adapter, built on the official `anthropic` Python SDK.

Notes on the choices here:

* **Streaming everywhere.** Screenwriting turns produce long outputs; the SDK
  requires streaming for large `max_tokens` so the request doesn't hit an HTTP
  timeout. We use `.get_final_message()` since we don't render tokens live.
* **Adaptive thinking** on the models that support it, with `output_config.effort`
  as the depth dial. `budget_tokens` is gone on the Claude 5 family.
* **Refusal fallbacks.** A drama generator legitimately writes conflict, threat
  and violence, so a safety decline on one model is a normal operating event.
  Server-side fallbacks re-run the request on a sibling model inside the same
  call instead of failing the shot.
* **Prompt caching** on the system block — the show bible is resent on every
  agent call, and caching it is the single biggest cost lever in the pipeline.
"""
from __future__ import annotations

import asyncio

from nexus.config import settings
from nexus.providers._json import extract_json, schema_instructions
from nexus.providers.base import Provider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import LLMRequest, ModelResponse, Usage
from nexus.util.errors import ProviderError
from nexus.util.logging import get_logger

log = get_logger(__name__)

# Models that accept adaptive thinking and output_config.effort.
_ADAPTIVE_MODELS = {"claude-opus-5", "claude-fable-5", "claude-sonnet-5",
                    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                    "claude-sonnet-4-6"}
# Models where a policy decline should be rescued by a sibling model.
_FALLBACK_SOURCES = {"claude-opus-5": "claude-opus-4-8", "claude-fable-5": "claude-opus-4-8"}
_FALLBACK_BETA = "server-side-fallback-2026-06-01"


class AnthropicProvider(Provider):
    id = "anthropic"

    def __init__(self) -> None:
        self._client = None
        self._lock = asyncio.Lock()

    async def _get_client(self):
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    try:
                        import anthropic
                    except ImportError as exc:  # pragma: no cover
                        raise ProviderError(
                            "the `anthropic` package is required for the Anthropic provider",
                            provider=self.id, retryable=False,
                        ) from exc
                    if not settings.anthropic_api_key:
                        raise ProviderError(
                            "ANTHROPIC_API_KEY is not configured",
                            provider=self.id, retryable=False,
                        )
                    self._client = anthropic.AsyncAnthropic(
                        api_key=settings.anthropic_api_key,
                        timeout=float(settings.llm_timeout_s),
                        max_retries=2,
                    )
        return self._client

    async def aclose(self) -> None:
        self._client = None

    def _build_content(self, req: LLMRequest) -> list[dict]:
        blocks: list[dict] = []
        for url in req.image_urls:
            blocks.append({"type": "image", "source": {"type": "url", "url": url}})
        blocks.append({"type": "text", "text": req.prompt})
        return blocks

    async def generate_text(self, spec: ModelSpec, req: LLMRequest) -> ModelResponse:
        import anthropic

        client = await self._get_client()
        model = spec.provider_model

        system_text = req.system
        if req.json_schema:
            system_text = f"{system_text}\n\n{schema_instructions(req.json_schema)}"

        # Caching the system block: the series bible is identical across the
        # dozens of agent calls in one episode.
        system_blocks = [{"type": "text", "text": system_text}]
        if req.cache_prefix and len(system_text) > 2000:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        params: dict = {
            "model": model,
            "max_tokens": min(req.max_tokens, spec.max_output_tokens or req.max_tokens),
            "system": system_blocks,
            "messages": [{"role": "user", "content": self._build_content(req)}],
        }
        if model in _ADAPTIVE_MODELS:
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": req.effort or "high"}
        elif req.temperature is not None:
            params["temperature"] = req.temperature

        fallback_model = _FALLBACK_SOURCES.get(model)

        try:
            if fallback_model:
                stream_ctx = client.beta.messages.stream(
                    betas=[_FALLBACK_BETA],
                    fallbacks=[{"model": fallback_model}],
                    **params,
                )
            else:
                stream_ctx = client.messages.stream(**params)

            async with stream_ctx as stream:
                message = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise ProviderError(f"rate limited: {exc}", provider=self.id, model=model,
                                status=429, retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(str(exc), provider=self.id, model=model,
                                status=exc.status_code, retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}", provider=self.id, model=model) from exc

        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            raise ProviderError(
                f"model declined the request (category: {getattr(details, 'category', 'unknown')})",
                provider=self.id, model=model, retryable=True,
                detail={"explanation": getattr(details, "explanation", None)},
            )

        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        if not text.strip():
            raise ProviderError("empty completion", provider=self.id, model=model)

        u = message.usage
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cached_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        parsed = extract_json(text) if req.json_schema else None
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=req.modality,
            text=text, parsed=parsed, usage=usage,
            raw={"stop_reason": getattr(message, "stop_reason", None),
                 "served_model": getattr(message, "model", model)},
        )


register(AnthropicProvider.id, AnthropicProvider)
