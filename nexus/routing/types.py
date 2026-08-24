"""Provider-neutral request/response envelopes used by the router."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class Modality(str, Enum):
    TEXT = "text"          # LLM reasoning / writing
    VISION = "vision"      # image understanding (critic, QA)
    IMAGE = "image"        # text-to-image / image-edit (keyframes, portraits)
    VIDEO = "video"        # text-to-video / image-to-video
    TTS = "tts"            # speech synthesis
    MUSIC = "music"        # score / music bed
    SFX = "sfx"            # sound effects / ambience


class Tier(str, Enum):
    """Commercial tier. `FREE` costs nothing to call (free API tiers,
    self-hosted, or open-weight endpoints with a free allowance)."""

    FREE = "free"
    BUDGET = "budget"
    STANDARD = "standard"
    PREMIUM = "premium"
    FLAGSHIP = "flagship"

    @property
    def rank(self) -> int:
        return {"free": 0, "budget": 1, "standard": 2, "premium": 3, "flagship": 4}[self.value]


class Quality(int, Enum):
    """Coarse quality band used for ordering candidates."""

    BASIC = 1
    GOOD = 2
    STRONG = 3
    EXCELLENT = 4
    BEST = 5


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    seconds: float = 0.0        # video/audio seconds produced
    images: int = 0
    characters: int = 0         # TTS characters

    def merge(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.seconds + other.seconds,
            self.images + other.images,
            self.characters + other.characters,
        )


# ── Requests ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LLMRequest:
    system: str
    prompt: str
    modality: Modality = Modality.TEXT
    json_schema: dict[str, Any] | None = None
    max_tokens: int = 16000
    temperature: float | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    image_urls: list[str] = field(default_factory=list)   # for Modality.VISION
    stop: list[str] = field(default_factory=list)
    cache_prefix: bool = True
    label: str = "llm"


@dataclass(slots=True)
class ImageRequest:
    prompt: str
    negative_prompt: str = ""
    reference_image_urls: list[str] = field(default_factory=list)
    edit_source_url: str | None = None
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    seed: int | None = None
    n: int = 1
    label: str = "image"


@dataclass(slots=True)
class VideoRequest:
    prompt: str
    negative_prompt: str = ""
    first_frame_url: str | None = None            # image-to-video anchor
    last_frame_url: str | None = None             # first/last-frame interpolation
    reference_image_urls: list[str] = field(default_factory=list)  # identity anchors
    duration_s: float = 5.0
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    fps: int = 24
    with_audio: bool = False
    camera_hint: str = ""
    seed: int | None = None
    label: str = "video"


@dataclass(slots=True)
class TTSRequest:
    text: str
    voice_id: str | None = None
    language: str = "en"
    speed: float = 1.0
    stability: float = 0.5
    style: str = ""
    emotion: str = ""
    label: str = "tts"


@dataclass(slots=True)
class AudioRequest:
    """Music bed, ambience or one-shot SFX."""

    prompt: str
    duration_s: float = 30.0
    kind: Literal["music", "ambience", "sfx"] = "music"
    label: str = "audio"


# ── Responses ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class ModelResponse:
    """Uniform result. Exactly one of `text` / `data` / `remote_url` is set."""

    provider: str
    model_id: str
    modality: Modality
    text: str | None = None
    parsed: Any = None
    data: bytes | None = None
    remote_url: str | None = None
    content_type: str = ""
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    duration_s: float = 0.0
    attempts: int = 1
    fallback_chain: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def used_fallback(self) -> bool:
        return len(self.fallback_chain) > 1
