"""The model catalog — every model the platform can route to.

Design notes
------------
*Pricing.*  Anthropic rates are first-party and exact.  Third-party rates move
constantly, so every non-Anthropic entry is a **seed default** carrying a
`price_confidence` marker.  Override any of them without touching code by
pointing ``NEXUS_MODEL_PRICING_FILE`` at a JSON file::

    {"fal/veo-3.1": {"usd_per_second": 0.40}, "openrouter/deepseek-v3": {"usd_per_1m_output": 0.9}}

*Availability.*  A model is only routable when its provider credential is
present.  That is what lets the same catalog serve a zero-key local dev box
(everything falls through to the free tier and the offline simulator) and a
fully-provisioned production deployment.

*Tiers.*  ``Tier.FREE`` means the call costs the operator nothing — a provider
free allowance, an open-weight endpoint, a self-hosted runtime, or the built-in
simulator.  Free models are what a trial user's job runs on.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace

from nexus.config import settings
from nexus.routing.types import Modality, Quality, Tier


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str                       # canonical "provider/model" key used everywhere
    provider: str                 # adapter id
    provider_model: str           # the string the provider's API expects
    modality: Modality
    tier: Tier
    quality: Quality
    display_name: str = ""
    description: str = ""

    # Pricing (only the fields relevant to the modality are used)
    usd_per_1m_input: float = 0.0
    usd_per_1m_output: float = 0.0
    usd_per_1m_cache_read: float = 0.0
    usd_per_image: float = 0.0
    usd_per_second: float = 0.0        # video / music
    usd_per_1k_chars: float = 0.0      # tts
    price_confidence: str = "seed"     # "exact" for first-party rates we control

    # Capabilities
    context_window: int = 0
    max_output_tokens: int = 0
    supports_json_schema: bool = False
    supports_vision: bool = False
    supports_thinking: bool = False

    supports_text_to_video: bool = False
    supports_image_to_video: bool = False
    supports_reference_images: bool = False   # identity anchoring — the key one
    max_reference_images: int = 0
    supports_first_last_frame: bool = False
    supports_native_audio: bool = False
    min_duration_s: float = 0.0
    max_duration_s: float = 0.0
    resolutions: tuple[str, ...] = ()
    aspect_ratios: tuple[str, ...] = ()

    supports_image_edit: bool = False         # image-to-image with references
    supports_voice_cloning: bool = False

    # Operational
    credential_field: str | None = None       # Settings attribute that must be set
    typical_latency_s: float = 10.0
    concurrency_limit: int = 4
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_free(self) -> bool:
        return self.tier is Tier.FREE

    def available(self) -> bool:
        if self.credential_field is None:
            return True
        return bool(getattr(settings, self.credential_field, None))

    def estimate_cost(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        seconds: float = 0.0,
        images: int = 0,
        characters: int = 0,
    ) -> float:
        total = 0.0
        total += (input_tokens / 1_000_000) * self.usd_per_1m_input
        total += (output_tokens / 1_000_000) * self.usd_per_1m_output
        total += (cached_input_tokens / 1_000_000) * (
            self.usd_per_1m_cache_read or self.usd_per_1m_input * 0.1
        )
        total += seconds * self.usd_per_second
        total += images * self.usd_per_image
        total += (characters / 1000) * self.usd_per_1k_chars
        return round(total, 6)

    def supports_duration(self, seconds: float) -> bool:
        if not self.max_duration_s:
            return True
        lo = self.min_duration_s or 0.0
        return lo - 1e-6 <= seconds <= self.max_duration_s + 1e-6

    def clamp_duration(self, seconds: float) -> float:
        if not self.max_duration_s:
            return seconds
        return max(self.min_duration_s or 1.0, min(seconds, self.max_duration_s))

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "display_name": self.display_name or self.provider_model,
            "modality": self.modality.value,
            "tier": self.tier.value,
            "quality": int(self.quality),
            "description": self.description,
            "available": self.available(),
            "pricing": {
                "usd_per_1m_input": self.usd_per_1m_input,
                "usd_per_1m_output": self.usd_per_1m_output,
                "usd_per_image": self.usd_per_image,
                "usd_per_second": self.usd_per_second,
                "usd_per_1k_chars": self.usd_per_1k_chars,
                "confidence": self.price_confidence,
            },
            "capabilities": {
                "context_window": self.context_window,
                "json_schema": self.supports_json_schema,
                "vision": self.supports_vision,
                "text_to_video": self.supports_text_to_video,
                "image_to_video": self.supports_image_to_video,
                "reference_images": self.supports_reference_images,
                "max_reference_images": self.max_reference_images,
                "first_last_frame": self.supports_first_last_frame,
                "native_audio": self.supports_native_audio,
                "image_edit": self.supports_image_edit,
                "max_duration_s": self.max_duration_s,
                "resolutions": list(self.resolutions),
            },
            "notes": self.notes,
            "tags": list(self.tags),
        }


# ══════════════════════════════════════════════════════════════════════════════
# TEXT — reasoning, writing, structured planning
# ══════════════════════════════════════════════════════════════════════════════

_TEXT: list[ModelSpec] = [
    # ── Anthropic (first-party rates, exact) ──────────────────────────────
    ModelSpec(
        id="anthropic/claude-opus-5", provider="anthropic", provider_model="claude-opus-5",
        modality=Modality.TEXT, tier=Tier.FLAGSHIP, quality=Quality.BEST,
        display_name="Claude Opus 5",
        description="Best-in-class long-horizon story planning and screenwriting.",
        usd_per_1m_input=5.0, usd_per_1m_output=25.0, usd_per_1m_cache_read=0.5,
        price_confidence="exact", context_window=1_000_000, max_output_tokens=128_000,
        supports_json_schema=True, supports_vision=True, supports_thinking=True,
        credential_field="anthropic_api_key", typical_latency_s=25.0,
        tags=("showrunner", "screenwriter", "continuity"),
    ),
    ModelSpec(
        id="anthropic/claude-fable-5", provider="anthropic", provider_model="claude-fable-5",
        modality=Modality.TEXT, tier=Tier.FLAGSHIP, quality=Quality.BEST,
        display_name="Claude Fable 5",
        description="Anthropic's most capable model; thinking always on.",
        usd_per_1m_input=10.0, usd_per_1m_output=50.0, usd_per_1m_cache_read=1.0,
        price_confidence="exact", context_window=1_000_000, max_output_tokens=128_000,
        supports_json_schema=True, supports_vision=True, supports_thinking=True,
        credential_field="anthropic_api_key", typical_latency_s=45.0,
        notes="Thinking cannot be disabled; effort controls depth.",
        tags=("showrunner", "premium"),
    ),
    ModelSpec(
        id="anthropic/claude-sonnet-5", provider="anthropic", provider_model="claude-sonnet-5",
        modality=Modality.TEXT, tier=Tier.PREMIUM, quality=Quality.EXCELLENT,
        display_name="Claude Sonnet 5",
        description="High-volume workhorse for scene and shot decomposition.",
        usd_per_1m_input=3.0, usd_per_1m_output=15.0, usd_per_1m_cache_read=0.3,
        price_confidence="exact", context_window=1_000_000, max_output_tokens=128_000,
        supports_json_schema=True, supports_vision=True, supports_thinking=True,
        credential_field="anthropic_api_key", typical_latency_s=15.0,
        tags=("storyboard", "prompt_smith"),
    ),
    ModelSpec(
        id="anthropic/claude-haiku-4-5", provider="anthropic", provider_model="claude-haiku-4-5",
        modality=Modality.TEXT, tier=Tier.STANDARD, quality=Quality.STRONG,
        display_name="Claude Haiku 4.5",
        description="Fast and cheap; good for per-shot prompt expansion at scale.",
        usd_per_1m_input=1.0, usd_per_1m_output=5.0, usd_per_1m_cache_read=0.1,
        price_confidence="exact", context_window=200_000, max_output_tokens=64_000,
        supports_json_schema=True, supports_vision=True,
        credential_field="anthropic_api_key", typical_latency_s=6.0,
        tags=("prompt_smith", "fast"),
    ),

    # ── OpenAI ────────────────────────────────────────────────────────────
    ModelSpec(
        id="openai/gpt-5.2", provider="openai", provider_model="gpt-5.2",
        modality=Modality.TEXT, tier=Tier.PREMIUM, quality=Quality.EXCELLENT,
        display_name="GPT-5.2", description="Strong structured-output planner.",
        usd_per_1m_input=1.25, usd_per_1m_output=10.0, context_window=400_000,
        max_output_tokens=128_000, supports_json_schema=True, supports_vision=True,
        credential_field="openai_api_key", typical_latency_s=18.0,
    ),
    ModelSpec(
        id="openai/gpt-5.2-mini", provider="openai", provider_model="gpt-5.2-mini",
        modality=Modality.TEXT, tier=Tier.STANDARD, quality=Quality.STRONG,
        display_name="GPT-5.2 mini", usd_per_1m_input=0.25, usd_per_1m_output=2.0,
        context_window=400_000, max_output_tokens=64_000, supports_json_schema=True,
        supports_vision=True, credential_field="openai_api_key", typical_latency_s=8.0,
    ),

    # ── Google ────────────────────────────────────────────────────────────
    ModelSpec(
        id="google/gemini-3-pro", provider="google", provider_model="gemini-3-pro",
        modality=Modality.TEXT, tier=Tier.PREMIUM, quality=Quality.EXCELLENT,
        display_name="Gemini 3 Pro", description="Huge context; strong at scene grids.",
        usd_per_1m_input=1.25, usd_per_1m_output=10.0, context_window=1_000_000,
        max_output_tokens=64_000, supports_json_schema=True, supports_vision=True,
        credential_field="google_api_key", typical_latency_s=16.0,
    ),
    ModelSpec(
        id="google/gemini-3-flash", provider="google", provider_model="gemini-3-flash",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.STRONG,
        display_name="Gemini 3 Flash (free tier)",
        description="Generous free allowance — the default engine for trial jobs.",
        usd_per_1m_input=0.0, usd_per_1m_output=0.0, context_window=1_000_000,
        max_output_tokens=64_000, supports_json_schema=True, supports_vision=True,
        credential_field="google_api_key", typical_latency_s=6.0,
        notes="Free tier is rate limited; router treats 429 as a fallback trigger.",
        tags=("free",),
    ),

    # ── Groq (free/near-free, very fast open weights) ─────────────────────
    ModelSpec(
        id="groq/llama-4-scout", provider="groq", provider_model="llama-4-scout-17b-16e-instruct",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="Llama 4 Scout (Groq)",
        description="Free, extremely fast. Good for mechanical prompt expansion.",
        context_window=131_072, max_output_tokens=8192, supports_json_schema=True,
        credential_field="groq_api_key", typical_latency_s=2.0, tags=("free", "fast"),
    ),
    ModelSpec(
        id="groq/llama-3.3-70b", provider="groq", provider_model="llama-3.3-70b-versatile",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="Llama 3.3 70B (Groq)", context_window=131_072,
        max_output_tokens=32_768, supports_json_schema=True,
        credential_field="groq_api_key", typical_latency_s=3.0, tags=("free", "fast"),
    ),
    ModelSpec(
        id="groq/qwen3-32b", provider="groq", provider_model="qwen3-32b",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="Qwen3 32B (Groq)", context_window=131_072, max_output_tokens=16_384,
        supports_json_schema=True, credential_field="groq_api_key", typical_latency_s=3.0,
        tags=("free",),
    ),

    # ── DeepSeek / budget open weights ────────────────────────────────────
    ModelSpec(
        id="deepseek/deepseek-v3.2", provider="deepseek", provider_model="deepseek-chat",
        modality=Modality.TEXT, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="DeepSeek V3.2", description="Very cheap, competent long-form writer.",
        usd_per_1m_input=0.28, usd_per_1m_output=0.42, context_window=131_072,
        max_output_tokens=32_768, supports_json_schema=True,
        credential_field="deepseek_api_key", typical_latency_s=20.0, tags=("budget",),
    ),
    ModelSpec(
        id="deepseek/deepseek-reasoner", provider="deepseek", provider_model="deepseek-reasoner",
        modality=Modality.TEXT, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="DeepSeek Reasoner", usd_per_1m_input=0.28, usd_per_1m_output=0.42,
        context_window=131_072, max_output_tokens=64_000, supports_json_schema=True,
        supports_thinking=True, credential_field="deepseek_api_key", typical_latency_s=40.0,
        tags=("budget",),
    ),

    # ── OpenRouter aggregator: one key, hundreds of models ────────────────
    ModelSpec(
        id="openrouter/claude-opus-5", provider="openrouter", provider_model="anthropic/claude-opus-5",
        modality=Modality.TEXT, tier=Tier.FLAGSHIP, quality=Quality.BEST,
        display_name="Claude Opus 5 (via OpenRouter)",
        usd_per_1m_input=5.0, usd_per_1m_output=25.0, context_window=1_000_000,
        max_output_tokens=64_000, supports_json_schema=True, supports_vision=True,
        credential_field="openrouter_api_key", typical_latency_s=25.0,
    ),
    ModelSpec(
        id="openrouter/gemini-3-pro", provider="openrouter", provider_model="google/gemini-3-pro",
        modality=Modality.TEXT, tier=Tier.PREMIUM, quality=Quality.EXCELLENT,
        display_name="Gemini 3 Pro (via OpenRouter)", usd_per_1m_input=1.25,
        usd_per_1m_output=10.0, context_window=1_000_000, max_output_tokens=64_000,
        supports_json_schema=True, supports_vision=True,
        credential_field="openrouter_api_key", typical_latency_s=16.0,
    ),
    ModelSpec(
        id="openrouter/deepseek-v3.2", provider="openrouter", provider_model="deepseek/deepseek-chat",
        modality=Modality.TEXT, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="DeepSeek V3.2 (via OpenRouter)", usd_per_1m_input=0.28,
        usd_per_1m_output=0.42, context_window=131_072, max_output_tokens=32_768,
        supports_json_schema=True, credential_field="openrouter_api_key",
        typical_latency_s=22.0, tags=("budget",),
    ),
    ModelSpec(
        id="openrouter/qwen3-235b-free", provider="openrouter",
        provider_model="qwen/qwen3-235b-a22b:free",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="Qwen3 235B (OpenRouter free)",
        description="Zero-cost OpenRouter free-pool endpoint.",
        context_window=131_072, max_output_tokens=16_384, supports_json_schema=True,
        credential_field="openrouter_api_key", typical_latency_s=25.0,
        notes="Free pool is heavily rate limited and can queue.", tags=("free",),
    ),
    ModelSpec(
        id="openrouter/llama-4-maverick-free", provider="openrouter",
        provider_model="meta-llama/llama-4-maverick:free",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="Llama 4 Maverick (OpenRouter free)", context_window=131_072,
        max_output_tokens=8192, supports_json_schema=True, supports_vision=True,
        credential_field="openrouter_api_key", typical_latency_s=25.0, tags=("free",),
    ),

    # ── Self-hosted ───────────────────────────────────────────────────────
    ModelSpec(
        id="ollama/qwen3", provider="ollama", provider_model="qwen3:14b",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Qwen3 14B (local Ollama)",
        description="Runs on your own hardware; zero marginal cost.",
        context_window=32_768, max_output_tokens=8192, supports_json_schema=True,
        credential_field="ollama_base_url", typical_latency_s=40.0, tags=("free", "self_hosted"),
    ),

    # ── Always-available offline engine ───────────────────────────────────
    ModelSpec(
        id="simulation/story-engine", provider="simulation", provider_model="story-engine",
        modality=Modality.TEXT, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Offline Story Engine",
        description="Deterministic local generator. Keeps the pipeline runnable with "
                    "zero API keys — used for dev, CI and demo mode.",
        context_window=1_000_000, max_output_tokens=128_000, supports_json_schema=True,
        supports_vision=True, typical_latency_s=0.05, concurrency_limit=32,
        tags=("free", "offline", "simulation"),
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE — character portraits, location plates, shot keyframes
# ══════════════════════════════════════════════════════════════════════════════

_IMAGE: list[ModelSpec] = [
    ModelSpec(
        id="fal/nano-banana-pro", provider="fal",
        provider_model="fal-ai/gemini-3-pro-image/edit",
        modality=Modality.IMAGE, tier=Tier.PREMIUM, quality=Quality.BEST,
        display_name="Nano Banana Pro (Gemini 3 Pro Image)",
        description="Best-in-class character-consistent image editing from references. "
                    "The default keyframe engine.",
        usd_per_image=0.14, supports_image_edit=True, supports_reference_images=True,
        max_reference_images=6, resolutions=("1080p", "2k", "4k"),
        aspect_ratios=("16:9", "9:16", "1:1", "4:5", "2.39:1"),
        credential_field="fal_api_key", typical_latency_s=18.0, concurrency_limit=8,
        tags=("keyframe", "identity"),
    ),
    ModelSpec(
        id="fal/seedream-4", provider="fal", provider_model="fal-ai/bytedance/seedream/v4/edit",
        modality=Modality.IMAGE, tier=Tier.STANDARD, quality=Quality.EXCELLENT,
        display_name="Seedream 4 Edit",
        description="Multi-reference editing with strong likeness retention.",
        usd_per_image=0.03, supports_image_edit=True, supports_reference_images=True,
        max_reference_images=6, resolutions=("1080p", "2k", "4k"),
        aspect_ratios=("16:9", "9:16", "1:1", "4:5"),
        credential_field="fal_api_key", typical_latency_s=12.0, concurrency_limit=8,
        tags=("keyframe", "identity", "value"),
    ),
    ModelSpec(
        id="fal/flux-kontext-max", provider="fal", provider_model="fal-ai/flux-pro/kontext/max",
        modality=Modality.IMAGE, tier=Tier.STANDARD, quality=Quality.EXCELLENT,
        display_name="FLUX.1 Kontext [max]",
        description="In-context image editing; keeps a subject across wardrobe/pose changes.",
        usd_per_image=0.08, supports_image_edit=True, supports_reference_images=True,
        max_reference_images=1, resolutions=("1080p", "2k"),
        aspect_ratios=("16:9", "9:16", "1:1", "4:5", "2.39:1"),
        credential_field="fal_api_key", typical_latency_s=14.0, tags=("keyframe", "identity"),
    ),
    ModelSpec(
        id="fal/flux-1.1-pro", provider="fal", provider_model="fal-ai/flux-pro/v1.1-ultra",
        modality=Modality.IMAGE, tier=Tier.STANDARD, quality=Quality.EXCELLENT,
        display_name="FLUX 1.1 Pro Ultra",
        description="Text-to-image workhorse for canonical character portraits.",
        usd_per_image=0.06, resolutions=("1080p", "2k", "4k"),
        aspect_ratios=("16:9", "9:16", "1:1", "4:5", "2.39:1"),
        credential_field="fal_api_key", typical_latency_s=10.0, tags=("portrait",),
    ),
    ModelSpec(
        id="fal/flux-schnell", provider="fal", provider_model="fal-ai/flux/schnell",
        modality=Modality.IMAGE, tier=Tier.BUDGET, quality=Quality.GOOD,
        display_name="FLUX Schnell", description="Cheap, fast drafts and previz.",
        usd_per_image=0.003, resolutions=("720p", "1080p"),
        aspect_ratios=("16:9", "9:16", "1:1"),
        credential_field="fal_api_key", typical_latency_s=3.0, tags=("budget", "previz"),
    ),
    ModelSpec(
        id="google/gemini-3-pro-image", provider="google",
        provider_model="gemini-3-pro-image-preview",
        modality=Modality.IMAGE, tier=Tier.PREMIUM, quality=Quality.BEST,
        display_name="Gemini 3 Pro Image (direct)",
        description="Direct Google endpoint for reference-conditioned image editing.",
        usd_per_image=0.13, supports_image_edit=True, supports_reference_images=True,
        max_reference_images=6, resolutions=("1080p", "2k", "4k"),
        aspect_ratios=("16:9", "9:16", "1:1", "4:5"),
        credential_field="google_api_key", typical_latency_s=18.0, tags=("keyframe", "identity"),
    ),
    ModelSpec(
        id="replicate/flux-kontext-pro", provider="replicate",
        provider_model="black-forest-labs/flux-kontext-pro",
        modality=Modality.IMAGE, tier=Tier.STANDARD, quality=Quality.EXCELLENT,
        display_name="FLUX Kontext Pro (Replicate)", usd_per_image=0.04,
        supports_image_edit=True, supports_reference_images=True, max_reference_images=1,
        resolutions=("1080p",), aspect_ratios=("16:9", "9:16", "1:1"),
        credential_field="replicate_api_token", typical_latency_s=15.0,
    ),
    ModelSpec(
        id="openai/gpt-image-1", provider="openai", provider_model="gpt-image-1",
        modality=Modality.IMAGE, tier=Tier.STANDARD, quality=Quality.STRONG,
        display_name="GPT Image 1", usd_per_image=0.04, supports_image_edit=True,
        supports_reference_images=True, max_reference_images=4,
        resolutions=("1080p",), aspect_ratios=("16:9", "9:16", "1:1"),
        credential_field="openai_api_key", typical_latency_s=20.0,
    ),
    ModelSpec(
        id="huggingface/flux-schnell-free", provider="huggingface",
        provider_model="black-forest-labs/FLUX.1-schnell",
        modality=Modality.IMAGE, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="FLUX Schnell (HF Inference, free tier)",
        description="Free-tier text-to-image for trial accounts.",
        resolutions=("720p", "1080p"), aspect_ratios=("16:9", "9:16", "1:1"),
        credential_field="huggingface_api_key", typical_latency_s=25.0, tags=("free",),
    ),
    ModelSpec(
        id="pollinations/flux-free", provider="pollinations", provider_model="flux",
        modality=Modality.IMAGE, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Pollinations FLUX (keyless free)",
        description="Keyless public endpoint. Last free resort before the simulator.",
        resolutions=("720p", "1080p"), aspect_ratios=("16:9", "9:16", "1:1"),
        credential_field=None, typical_latency_s=20.0, concurrency_limit=2,
        notes="No SLA, no identity conditioning. Draft quality only.", tags=("free", "keyless"),
    ),
    ModelSpec(
        id="simulation/image", provider="simulation", provider_model="image",
        modality=Modality.IMAGE, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Offline Image Synth",
        description="Renders a deterministic, readable placeholder plate locally.",
        resolutions=("720p", "1080p"), aspect_ratios=("16:9", "9:16", "1:1", "4:5", "2.39:1"),
        supports_image_edit=True, supports_reference_images=True, max_reference_images=8,
        typical_latency_s=0.2, concurrency_limit=16, tags=("free", "offline", "simulation"),
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO — the expensive half. Reference-image support is what buys consistency.
# ══════════════════════════════════════════════════════════════════════════════

_VIDEO: list[ModelSpec] = [
    ModelSpec(
        id="fal/veo-3.1", provider="fal", provider_model="fal-ai/veo3.1",
        modality=Modality.VIDEO, tier=Tier.FLAGSHIP, quality=Quality.BEST,
        display_name="Veo 3.1",
        description="Native audio and 'ingredients' multi-reference conditioning.",
        usd_per_second=0.40, supports_text_to_video=True, supports_image_to_video=True,
        supports_reference_images=True, max_reference_images=3,
        supports_native_audio=True, min_duration_s=4.0, max_duration_s=8.0,
        resolutions=("720p", "1080p"), aspect_ratios=("16:9", "9:16"),
        credential_field="fal_api_key", typical_latency_s=150.0, concurrency_limit=4,
        tags=("premium", "identity", "audio"),
    ),
    ModelSpec(
        id="fal/kling-3.0-pro", provider="fal", provider_model="fal-ai/kling-video/v3/pro/image-to-video",
        modality=Modality.VIDEO, tier=Tier.PREMIUM, quality=Quality.BEST,
        display_name="Kling 3.0 Pro",
        description="Strongest facial-identity and wardrobe retention across cuts. "
                    "Default drama engine.",
        usd_per_second=0.16, supports_text_to_video=True, supports_image_to_video=True,
        supports_reference_images=True, max_reference_images=4,
        supports_first_last_frame=True, min_duration_s=5.0, max_duration_s=10.0,
        resolutions=("720p", "1080p"), aspect_ratios=("16:9", "9:16", "1:1"),
        credential_field="fal_api_key", typical_latency_s=210.0, concurrency_limit=6,
        tags=("premium", "identity", "drama"),
    ),
    ModelSpec(
        id="fal/seedance-2.5", provider="fal", provider_model="fal-ai/bytedance/seedance/v2.5/pro",
        modality=Modality.VIDEO, tier=Tier.PREMIUM, quality=Quality.EXCELLENT,
        display_name="Seedance 2.5 Pro",
        description="Multi-reference, multi-shot storyboarding with native audio.",
        usd_per_second=0.30, supports_text_to_video=True, supports_image_to_video=True,
        supports_reference_images=True, max_reference_images=4, supports_native_audio=True,
        min_duration_s=4.0, max_duration_s=12.0, resolutions=("720p", "1080p"),
        aspect_ratios=("16:9", "9:16", "1:1"), credential_field="fal_api_key",
        typical_latency_s=180.0, concurrency_limit=4, tags=("premium", "identity", "audio"),
    ),
    ModelSpec(
        id="runway/gen-4.5", provider="runway", provider_model="gen4_turbo",
        modality=Modality.VIDEO, tier=Tier.PREMIUM, quality=Quality.EXCELLENT,
        display_name="Runway Gen-4.5",
        description="Reference-driven world consistency with fine camera control.",
        usd_per_second=0.20, supports_text_to_video=True, supports_image_to_video=True,
        supports_reference_images=True, max_reference_images=3,
        min_duration_s=5.0, max_duration_s=10.0, resolutions=("720p", "1080p"),
        aspect_ratios=("16:9", "9:16", "1:1"), credential_field="runway_api_key",
        typical_latency_s=120.0, concurrency_limit=3, tags=("premium", "identity"),
    ),
    ModelSpec(
        id="fal/kling-2.5-standard", provider="fal",
        provider_model="fal-ai/kling-video/v2.5-turbo/standard/image-to-video",
        modality=Modality.VIDEO, tier=Tier.STANDARD, quality=Quality.STRONG,
        display_name="Kling 2.5 Turbo Standard",
        description="Good identity retention at roughly a third of Pro cost.",
        usd_per_second=0.05, supports_image_to_video=True, supports_reference_images=True,
        max_reference_images=1, supports_first_last_frame=True,
        min_duration_s=5.0, max_duration_s=10.0, resolutions=("720p", "1080p"),
        aspect_ratios=("16:9", "9:16", "1:1"), credential_field="fal_api_key",
        typical_latency_s=140.0, concurrency_limit=8, tags=("value", "identity"),
    ),
    ModelSpec(
        id="fal/wan-2.5", provider="fal", provider_model="fal-ai/wan/v2.5/image-to-video",
        modality=Modality.VIDEO, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="Wan 2.5",
        description="Open-weight; the cost floor for paid image-to-video.",
        usd_per_second=0.05, supports_text_to_video=True, supports_image_to_video=True,
        min_duration_s=5.0, max_duration_s=10.0, resolutions=("480p", "720p", "1080p"),
        aspect_ratios=("16:9", "9:16", "1:1"), credential_field="fal_api_key",
        typical_latency_s=110.0, concurrency_limit=8, tags=("budget", "open_weights"),
    ),
    ModelSpec(
        id="fal/ltx-video-13b", provider="fal", provider_model="fal-ai/ltx-video-13b-distilled",
        modality=Modality.VIDEO, tier=Tier.BUDGET, quality=Quality.GOOD,
        display_name="LTX Video 13B", description="Fastest paid option; previz and drafts.",
        usd_per_second=0.02, supports_text_to_video=True, supports_image_to_video=True,
        min_duration_s=3.0, max_duration_s=9.0, resolutions=("480p", "720p"),
        aspect_ratios=("16:9", "9:16"), credential_field="fal_api_key",
        typical_latency_s=35.0, concurrency_limit=10, tags=("budget", "previz"),
    ),
    ModelSpec(
        id="replicate/kling-2.5", provider="replicate",
        provider_model="kwaivgi/kling-v2.5-turbo-pro",
        modality=Modality.VIDEO, tier=Tier.STANDARD, quality=Quality.STRONG,
        display_name="Kling 2.5 (Replicate)", usd_per_second=0.07,
        supports_image_to_video=True, supports_reference_images=True, max_reference_images=1,
        min_duration_s=5.0, max_duration_s=10.0, resolutions=("720p", "1080p"),
        aspect_ratios=("16:9", "9:16"), credential_field="replicate_api_token",
        typical_latency_s=160.0, concurrency_limit=4, tags=("identity",),
    ),
    ModelSpec(
        id="replicate/wan-2.2-i2v", provider="replicate", provider_model="wan-video/wan-2.2-i2v-fast",
        modality=Modality.VIDEO, tier=Tier.BUDGET, quality=Quality.GOOD,
        display_name="Wan 2.2 I2V Fast (Replicate)", usd_per_second=0.03,
        supports_image_to_video=True, min_duration_s=4.0, max_duration_s=8.0,
        resolutions=("480p", "720p"), aspect_ratios=("16:9", "9:16"),
        credential_field="replicate_api_token", typical_latency_s=80.0, tags=("budget",),
    ),
    ModelSpec(
        id="huggingface/ltx-video-free", provider="huggingface",
        provider_model="Lightricks/LTX-Video",
        modality=Modality.VIDEO, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="LTX Video (HF free tier)",
        description="Free-tier video for trial accounts. Queued and rate limited.",
        supports_text_to_video=True, supports_image_to_video=True,
        min_duration_s=2.0, max_duration_s=5.0, resolutions=("480p",),
        aspect_ratios=("16:9", "9:16"), credential_field="huggingface_api_key",
        typical_latency_s=180.0, concurrency_limit=1, tags=("free",),
    ),
    ModelSpec(
        id="simulation/video", provider="simulation", provider_model="video",
        modality=Modality.VIDEO, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Offline Video Synth",
        description="Ken-Burns animates the keyframe locally with ffmpeg. Always available, "
                    "so an episode always renders end to end.",
        supports_text_to_video=True, supports_image_to_video=True,
        supports_reference_images=True, max_reference_images=8,
        supports_first_last_frame=True, min_duration_s=1.0, max_duration_s=30.0,
        resolutions=("480p", "720p", "1080p"),
        aspect_ratios=("16:9", "9:16", "1:1", "4:5", "2.39:1"),
        typical_latency_s=3.0, concurrency_limit=8, tags=("free", "offline", "simulation"),
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# TTS + MUSIC
# ══════════════════════════════════════════════════════════════════════════════

_TTS: list[ModelSpec] = [
    ModelSpec(
        id="elevenlabs/eleven-v3", provider="elevenlabs", provider_model="eleven_v3",
        modality=Modality.TTS, tier=Tier.PREMIUM, quality=Quality.BEST,
        display_name="ElevenLabs v3",
        description="Most expressive dramatic delivery; emotional tags and voice cloning.",
        usd_per_1k_chars=0.18, supports_voice_cloning=True,
        credential_field="elevenlabs_api_key", typical_latency_s=6.0, concurrency_limit=6,
        tags=("dialogue", "premium"),
    ),
    ModelSpec(
        id="elevenlabs/turbo-v2.5", provider="elevenlabs", provider_model="eleven_turbo_v2_5",
        modality=Modality.TTS, tier=Tier.STANDARD, quality=Quality.EXCELLENT,
        display_name="ElevenLabs Turbo v2.5", usd_per_1k_chars=0.09,
        supports_voice_cloning=True, credential_field="elevenlabs_api_key",
        typical_latency_s=2.5, concurrency_limit=10, tags=("dialogue",),
    ),
    ModelSpec(
        id="cartesia/sonic-3", provider="cartesia", provider_model="sonic-3",
        modality=Modality.TTS, tier=Tier.STANDARD, quality=Quality.EXCELLENT,
        display_name="Cartesia Sonic 3", description="Very low latency, natural prosody.",
        usd_per_1k_chars=0.05, supports_voice_cloning=True,
        credential_field="cartesia_api_key", typical_latency_s=1.5, concurrency_limit=12,
    ),
    ModelSpec(
        id="openai/gpt-4o-mini-tts", provider="openai", provider_model="gpt-4o-mini-tts",
        modality=Modality.TTS, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="OpenAI gpt-4o-mini-tts",
        description="Steerable delivery via instructions; cheap.",
        usd_per_1k_chars=0.015, credential_field="openai_api_key", typical_latency_s=4.0,
        tags=("budget",),
    ),
    ModelSpec(
        id="fal/kokoro", provider="fal", provider_model="fal-ai/kokoro/american-english",
        modality=Modality.TTS, tier=Tier.BUDGET, quality=Quality.GOOD,
        display_name="Kokoro 82M", description="Open-weight TTS, extremely cheap.",
        usd_per_1k_chars=0.002, credential_field="fal_api_key", typical_latency_s=3.0,
        tags=("budget", "open_weights"),
    ),
    ModelSpec(
        id="edge/neural-tts", provider="edge", provider_model="edge-tts",
        modality=Modality.TTS, tier=Tier.FREE, quality=Quality.GOOD,
        display_name="Edge Neural TTS (free)",
        description="Free multilingual neural voices — the trial-tier dialogue engine.",
        credential_field=None, typical_latency_s=2.0, concurrency_limit=6, tags=("free",),
    ),
    ModelSpec(
        id="simulation/tts", provider="simulation", provider_model="tts",
        modality=Modality.TTS, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Offline Speech Synth",
        description="Generates correctly-timed silent/tone audio so timing and assembly "
                    "stay exact without any TTS provider.",
        typical_latency_s=0.1, concurrency_limit=16, tags=("free", "offline", "simulation"),
    ),
]

_MUSIC: list[ModelSpec] = [
    ModelSpec(
        id="elevenlabs/music", provider="elevenlabs", provider_model="eleven_music",
        modality=Modality.MUSIC, tier=Tier.PREMIUM, quality=Quality.BEST,
        display_name="ElevenLabs Music", description="Cinematic scoring from a text brief.",
        usd_per_second=0.02, min_duration_s=10.0, max_duration_s=300.0,
        credential_field="elevenlabs_api_key", typical_latency_s=60.0, concurrency_limit=2,
    ),
    ModelSpec(
        id="fal/stable-audio-2.5", provider="fal", provider_model="fal-ai/stable-audio-25/text-to-audio",
        modality=Modality.MUSIC, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="Stable Audio 2.5", usd_per_second=0.005, min_duration_s=5.0,
        max_duration_s=190.0, credential_field="fal_api_key", typical_latency_s=25.0,
        tags=("budget",),
    ),
    ModelSpec(
        id="fal/mmaudio-v2", provider="fal", provider_model="fal-ai/mmaudio-v2",
        modality=Modality.SFX, tier=Tier.BUDGET, quality=Quality.STRONG,
        display_name="MMAudio v2", description="Video-synchronised foley and ambience.",
        usd_per_second=0.004, min_duration_s=1.0, max_duration_s=30.0,
        credential_field="fal_api_key", typical_latency_s=20.0, tags=("sfx",),
    ),
    ModelSpec(
        id="simulation/music", provider="simulation", provider_model="music",
        modality=Modality.MUSIC, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Offline Score Synth",
        description="Procedural ambient bed rendered locally with ffmpeg.",
        min_duration_s=1.0, max_duration_s=1800.0, typical_latency_s=1.5,
        tags=("free", "offline", "simulation"),
    ),
    ModelSpec(
        id="simulation/sfx", provider="simulation", provider_model="sfx",
        modality=Modality.SFX, tier=Tier.FREE, quality=Quality.BASIC,
        display_name="Offline Ambience Synth", min_duration_s=1.0, max_duration_s=120.0,
        typical_latency_s=0.5, tags=("free", "offline", "simulation"),
    ),
]


# ── Registry ──────────────────────────────────────────────────────────────────

_ALL: list[ModelSpec] = [*_TEXT, *_IMAGE, *_VIDEO, *_TTS, *_MUSIC]


def _apply_price_overrides(specs: list[ModelSpec]) -> list[ModelSpec]:
    path = os.getenv("NEXUS_MODEL_PRICING_FILE")
    if not path or not os.path.exists(path):
        return specs
    with open(path, encoding="utf-8") as fh:
        overrides = json.load(fh)
    out = []
    for spec in specs:
        patch = overrides.get(spec.id)
        out.append(replace(spec, **patch, price_confidence="override") if patch else spec)
    return out


_ALL = _apply_price_overrides(_ALL)
CATALOG: dict[str, ModelSpec] = {spec.id: spec for spec in _ALL}

# Vision-capable text models double as the visual QA engine.
for _spec in list(CATALOG.values()):
    if _spec.modality is Modality.TEXT and _spec.supports_vision:
        _vid = f"{_spec.id}#vision"
        CATALOG[_vid] = replace(_spec, id=_vid, modality=Modality.VISION)


def get_spec(model_id: str) -> ModelSpec | None:
    return CATALOG.get(model_id)


def list_specs(
    modality: Modality | None = None,
    *,
    tier: Tier | None = None,
    available_only: bool = False,
    require: dict[str, bool] | None = None,
) -> list[ModelSpec]:
    out = []
    for spec in CATALOG.values():
        if modality and spec.modality is not modality:
            continue
        if tier and spec.tier is not tier:
            continue
        if available_only and not spec.available():
            continue
        if require and not all(getattr(spec, k, False) == v for k, v in require.items()):
            continue
        out.append(spec)
    return sorted(out, key=lambda s: (-int(s.quality), s.tier.rank, s.id))


def available_providers() -> dict[str, bool]:
    seen: dict[str, bool] = {}
    for spec in CATALOG.values():
        seen.setdefault(spec.provider, False)
        if spec.available():
            seen[spec.provider] = True
    return seen
