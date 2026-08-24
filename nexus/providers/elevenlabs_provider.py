"""ElevenLabs adapter — dialogue and score."""
from __future__ import annotations

from nexus.config import settings
from nexus.providers.base import HttpProvider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import AudioRequest, Modality, ModelResponse, TTSRequest, Usage
from nexus.util.errors import ProviderError

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # Rachel — a safe neutral default


class ElevenLabsProvider(HttpProvider):
    id = "elevenlabs"
    base_url = "https://api.elevenlabs.io/v1"

    def api_key(self) -> str:
        if not settings.elevenlabs_api_key:
            raise ProviderError("ELEVENLABS_API_KEY is not configured",
                                provider=self.id, retryable=False)
        return settings.elevenlabs_api_key

    def headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key(), "Content-Type": "application/json"}

    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        voice = req.voice_id or DEFAULT_VOICE
        body = {
            "text": req.text,
            "model_id": spec.provider_model,
            "voice_settings": {
                "stability": req.stability,
                "similarity_boost": 0.8,
                "style": 0.35 if req.emotion else 0.0,
                "use_speaker_boost": True,
                "speed": max(0.7, min(req.speed, 1.2)),
            },
        }
        resp = await self.request(
            "POST", f"/text-to-speech/{voice}?output_format=mp3_44100_128",
            json=body, model=spec.provider_model, expect_json=False, timeout_s=180.0,
        )
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.TTS,
                             data=resp.content, content_type="audio/mpeg",
                             usage=Usage(characters=len(req.text)))

    async def generate_audio(self, spec: ModelSpec, req: AudioRequest) -> ModelResponse:
        body = {"prompt": req.prompt[:2000], "music_length_ms": int(req.duration_s * 1000)}
        resp = await self.request("POST", "/music", json=body, model=spec.provider_model,
                                  expect_json=False, timeout_s=600.0)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.MUSIC,
                             data=resp.content, content_type="audio/mpeg",
                             duration_s=req.duration_s, usage=Usage(seconds=req.duration_s))


class CartesiaProvider(HttpProvider):
    id = "cartesia"
    base_url = "https://api.cartesia.ai"

    def headers(self) -> dict[str, str]:
        if not settings.cartesia_api_key:
            raise ProviderError("CARTESIA_API_KEY is not configured",
                                provider=self.id, retryable=False)
        return {
            "X-API-Key": settings.cartesia_api_key,
            "Cartesia-Version": "2024-11-13",
            "Content-Type": "application/json",
        }

    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        if not req.voice_id:
            raise ProviderError("Cartesia requires an explicit voice id",
                                provider=self.id, model=spec.provider_model, retryable=False)
        body = {
            "model_id": spec.provider_model,
            "transcript": req.text,
            "voice": {"mode": "id", "id": req.voice_id},
            "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
            "language": req.language,
        }
        resp = await self.request("POST", "/tts/bytes", json=body, model=spec.provider_model,
                                  expect_json=False, timeout_s=120.0)
        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.TTS,
                             data=resp.content, content_type="audio/mpeg",
                             usage=Usage(characters=len(req.text)))


register(ElevenLabsProvider.id, ElevenLabsProvider)
register(CartesiaProvider.id, CartesiaProvider)
