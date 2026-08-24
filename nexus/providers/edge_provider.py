"""Free neural TTS via Microsoft Edge voices (`edge-tts`).

This is what a trial-tier episode speaks with: no key, no cost, ~90 languages.
"""
from __future__ import annotations

import asyncio

from nexus.providers.base import Provider
from nexus.providers.registry import register
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import Modality, ModelResponse, TTSRequest, Usage
from nexus.util.errors import ProviderError

# A small cast of distinct free voices the casting agent can draw from.
VOICE_POOL = {
    "male_adult": ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-GB-RyanNeural",
                   "en-US-EricNeural", "en-AU-WilliamNeural"],
    "female_adult": ["en-US-JennyNeural", "en-US-AriaNeural", "en-GB-SoniaNeural",
                     "en-US-MichelleNeural", "en-AU-NatashaNeural"],
    "male_senior": ["en-GB-ThomasNeural", "en-US-RogerNeural"],
    "female_senior": ["en-GB-LibbyNeural", "en-US-NancyNeural"],
    "neutral": ["en-US-AndrewNeural", "en-US-EmmaNeural"],
}
DEFAULT_VOICE = "en-US-AndrewNeural"


def pick_voice(gender: str, age_bracket: str, taken: set[str]) -> str:
    key = f"{gender}_{'senior' if age_bracket in ('senior', 'elderly') else 'adult'}"
    pool = VOICE_POOL.get(key) or VOICE_POOL["neutral"]
    for voice in pool:
        if voice not in taken:
            return voice
    return pool[0]


class EdgeTTSProvider(Provider):
    id = "edge"

    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderError(
                "the `edge-tts` package is required for free TTS (pip install edge-tts)",
                provider=self.id, retryable=False,
            ) from exc

        rate_pct = round((req.speed - 1.0) * 100)
        rate = f"{rate_pct:+d}%"
        voice = req.voice_id or DEFAULT_VOICE

        communicate = edge_tts.Communicate(req.text, voice, rate=rate)
        chunks: list[bytes] = []
        try:
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ProviderError(f"edge-tts synthesis failed: {exc}",
                                provider=self.id, model=voice) from exc

        if not chunks:
            raise ProviderError("edge-tts returned no audio", provider=self.id, model=voice)

        return ModelResponse(provider=self.id, model_id=spec.id, modality=Modality.TTS,
                             data=b"".join(chunks), content_type="audio/mpeg",
                             usage=Usage(characters=len(req.text)))


register(EdgeTTSProvider.id, EdgeTTSProvider)
