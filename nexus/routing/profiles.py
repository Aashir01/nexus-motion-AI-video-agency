"""Routing profiles map a plan (or a user preference) onto model choice.

A profile answers two questions for every generation call:
  1. What is the most expensive tier we're allowed to touch?
  2. Which model do we *prefer* for this production role, if it's available?

Roles are named after the job they do in the pipeline, so the routing table
reads like a crew list.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nexus.routing.types import Modality, Tier

# Production roles → the modality they consume.
ROLE_MODALITY: dict[str, Modality] = {
    "showrunner": Modality.TEXT,
    "screenwriter": Modality.TEXT,
    "character_designer": Modality.TEXT,
    "location_designer": Modality.TEXT,
    "storyboard": Modality.TEXT,
    "continuity": Modality.TEXT,
    "prompt_smith": Modality.TEXT,
    "casting": Modality.TEXT,
    "critic": Modality.TEXT,
    "visual_critic": Modality.VISION,
    "portrait": Modality.IMAGE,
    "location_plate": Modality.IMAGE,
    "keyframe": Modality.IMAGE,
    "shot_video": Modality.VIDEO,
    "dialogue": Modality.TTS,
    "score": Modality.MUSIC,
    "ambience": Modality.SFX,
}


@dataclass(frozen=True, slots=True)
class RoutingProfile:
    name: str
    label: str
    description: str
    max_tier: Tier
    preferences: dict[str, list[str]] = field(default_factory=dict)
    quality_weight: float = 1.0
    cost_weight: float = 1.0
    allow_simulation: bool = True

    def preferred(self, role: str) -> list[str]:
        return self.preferences.get(role, [])


_FREE_PREFS = {
    "showrunner": ["google/gemini-3-flash", "groq/llama-3.3-70b", "openrouter/qwen3-235b-free", "simulation/story-engine"],
    "screenwriter": ["google/gemini-3-flash", "openrouter/qwen3-235b-free", "groq/llama-3.3-70b", "simulation/story-engine"],
    "storyboard": ["google/gemini-3-flash", "groq/llama-3.3-70b", "simulation/story-engine"],
    "prompt_smith": ["groq/llama-4-scout", "google/gemini-3-flash", "simulation/story-engine"],
    "continuity": ["google/gemini-3-flash", "groq/llama-3.3-70b", "simulation/story-engine"],
    "critic": ["groq/llama-3.3-70b", "google/gemini-3-flash", "simulation/story-engine"],
    "visual_critic": ["google/gemini-3-flash#vision", "simulation/story-engine#vision"],
    "portrait": ["huggingface/flux-schnell-free", "pollinations/flux-free", "simulation/image"],
    "keyframe": ["huggingface/flux-schnell-free", "pollinations/flux-free", "simulation/image"],
    "location_plate": ["pollinations/flux-free", "simulation/image"],
    "shot_video": ["huggingface/ltx-video-free", "simulation/video"],
    "dialogue": ["edge/neural-tts", "simulation/tts"],
    "score": ["simulation/music"],
    "ambience": ["simulation/sfx"],
}

PROFILES: dict[str, RoutingProfile] = {
    "offline": RoutingProfile(
        name="offline",
        label="Offline / Simulation",
        description="Never leaves the machine. Every role is served by the built-in "
                    "engine, so the full pipeline runs in CI and on a laptop with no keys.",
        max_tier=Tier.FREE,
        preferences={
            "showrunner": ["simulation/story-engine"], "screenwriter": ["simulation/story-engine"],
            "character_designer": ["simulation/story-engine"],
            "location_designer": ["simulation/story-engine"],
            "storyboard": ["simulation/story-engine"], "continuity": ["simulation/story-engine"],
            "prompt_smith": ["simulation/story-engine"], "casting": ["simulation/story-engine"],
            "critic": ["simulation/story-engine"], "visual_critic": ["simulation/story-engine#vision"],
            "portrait": ["simulation/image"], "location_plate": ["simulation/image"],
            "keyframe": ["simulation/image"], "shot_video": ["simulation/video"],
            "dialogue": ["simulation/tts"], "score": ["simulation/music"],
            "ambience": ["simulation/sfx"],
        },
        quality_weight=0.0, cost_weight=3.0,
    ),
    "free": RoutingProfile(
        name="free",
        label="Free / Demo",
        description="Zero marginal cost. Free provider tiers with an offline simulator "
                    "backstop, so a full episode always renders.",
        max_tier=Tier.FREE,
        preferences=_FREE_PREFS,
        quality_weight=0.4, cost_weight=2.0,
    ),
    "budget": RoutingProfile(
        name="budget",
        label="Budget",
        description="Cheapest paid models. Good for previz and rough cuts.",
        max_tier=Tier.BUDGET,
        preferences={
            "showrunner": ["deepseek/deepseek-v3.2", "openrouter/deepseek-v3.2", "google/gemini-3-flash"],
            "screenwriter": ["deepseek/deepseek-v3.2", "openrouter/deepseek-v3.2"],
            "storyboard": ["deepseek/deepseek-v3.2", "groq/llama-3.3-70b"],
            "prompt_smith": ["groq/llama-4-scout", "deepseek/deepseek-v3.2"],
            "critic": ["groq/llama-3.3-70b", "deepseek/deepseek-v3.2"],
            "portrait": ["fal/flux-schnell", "huggingface/flux-schnell-free"],
            "keyframe": ["fal/flux-schnell", "huggingface/flux-schnell-free"],
            "location_plate": ["fal/flux-schnell"],
            "shot_video": ["fal/ltx-video-13b", "replicate/wan-2.2-i2v", "fal/wan-2.5"],
            "dialogue": ["fal/kokoro", "openai/gpt-4o-mini-tts", "edge/neural-tts"],
            "score": ["fal/stable-audio-2.5", "simulation/music"],
            "ambience": ["fal/mmaudio-v2", "simulation/sfx"],
        },
        quality_weight=0.7, cost_weight=1.6,
    ),
    "balanced": RoutingProfile(
        name="balanced",
        label="Balanced (recommended)",
        description="Strong identity retention at sane cost: Seedream keyframes into "
                    "Kling 2.5, premium writing where it matters.",
        max_tier=Tier.STANDARD,
        preferences={
            "showrunner": ["anthropic/claude-sonnet-5", "google/gemini-3-pro", "deepseek/deepseek-v3.2"],
            "screenwriter": ["anthropic/claude-sonnet-5", "google/gemini-3-pro", "deepseek/deepseek-v3.2"],
            "character_designer": ["anthropic/claude-sonnet-5", "google/gemini-3-pro"],
            "storyboard": ["anthropic/claude-sonnet-5", "openai/gpt-5.2-mini", "deepseek/deepseek-v3.2"],
            "prompt_smith": ["anthropic/claude-haiku-4-5", "groq/llama-4-scout"],
            "continuity": ["anthropic/claude-haiku-4-5", "deepseek/deepseek-v3.2"],
            "critic": ["anthropic/claude-haiku-4-5", "groq/llama-3.3-70b"],
            "visual_critic": ["anthropic/claude-haiku-4-5#vision", "google/gemini-3-flash#vision"],
            "portrait": ["fal/flux-1.1-pro", "fal/seedream-4", "fal/flux-schnell"],
            "keyframe": ["fal/seedream-4", "fal/flux-kontext-max", "replicate/flux-kontext-pro"],
            "location_plate": ["fal/flux-1.1-pro", "fal/flux-schnell"],
            "shot_video": ["fal/kling-2.5-standard", "replicate/kling-2.5", "fal/wan-2.5"],
            "dialogue": ["elevenlabs/turbo-v2.5", "cartesia/sonic-3", "openai/gpt-4o-mini-tts"],
            "score": ["fal/stable-audio-2.5", "elevenlabs/music"],
            "ambience": ["fal/mmaudio-v2", "simulation/sfx"],
        },
        quality_weight=1.0, cost_weight=1.0,
    ),
    "premium": RoutingProfile(
        name="premium",
        label="Premium",
        description="Kling 3.0 Pro shots off Nano-Banana keyframes, ElevenLabs v3 dialogue.",
        max_tier=Tier.PREMIUM,
        preferences={
            "showrunner": ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"],
            "screenwriter": ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"],
            "character_designer": ["anthropic/claude-opus-5", "google/gemini-3-pro"],
            "storyboard": ["anthropic/claude-sonnet-5", "anthropic/claude-opus-5"],
            "prompt_smith": ["anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5"],
            "continuity": ["anthropic/claude-sonnet-5"],
            "critic": ["anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5"],
            "visual_critic": ["anthropic/claude-sonnet-5#vision", "google/gemini-3-pro#vision"],
            "portrait": ["fal/nano-banana-pro", "google/gemini-3-pro-image", "fal/seedream-4"],
            "keyframe": ["fal/nano-banana-pro", "fal/seedream-4", "fal/flux-kontext-max"],
            "location_plate": ["fal/nano-banana-pro", "fal/flux-1.1-pro"],
            "shot_video": ["fal/kling-3.0-pro", "runway/gen-4.5", "fal/seedance-2.5", "fal/kling-2.5-standard"],
            "dialogue": ["elevenlabs/eleven-v3", "elevenlabs/turbo-v2.5", "cartesia/sonic-3"],
            "score": ["elevenlabs/music", "fal/stable-audio-2.5"],
            "ambience": ["fal/mmaudio-v2"],
        },
        quality_weight=1.6, cost_weight=0.5,
    ),
    "flagship": RoutingProfile(
        name="flagship",
        label="Flagship",
        description="No compromises: Opus 5 writing, Veo 3.1 / Seedance native-audio shots.",
        max_tier=Tier.FLAGSHIP,
        preferences={
            "showrunner": ["anthropic/claude-opus-5", "anthropic/claude-fable-5"],
            "screenwriter": ["anthropic/claude-opus-5", "anthropic/claude-fable-5"],
            "character_designer": ["anthropic/claude-opus-5"],
            "storyboard": ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"],
            "prompt_smith": ["anthropic/claude-sonnet-5"],
            "continuity": ["anthropic/claude-opus-5"],
            "critic": ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"],
            "visual_critic": ["anthropic/claude-opus-5#vision"],
            "portrait": ["fal/nano-banana-pro", "google/gemini-3-pro-image"],
            "keyframe": ["fal/nano-banana-pro", "fal/seedream-4"],
            "location_plate": ["fal/nano-banana-pro"],
            "shot_video": ["fal/veo-3.1", "fal/kling-3.0-pro", "fal/seedance-2.5", "runway/gen-4.5"],
            "dialogue": ["elevenlabs/eleven-v3"],
            "score": ["elevenlabs/music"],
            "ambience": ["fal/mmaudio-v2"],
        },
        quality_weight=2.5, cost_weight=0.15,
    ),
}

DEFAULT_PROFILE = "balanced"


def get_profile(name: str | None) -> RoutingProfile:
    return PROFILES.get((name or DEFAULT_PROFILE).lower(), PROFILES[DEFAULT_PROFILE])
