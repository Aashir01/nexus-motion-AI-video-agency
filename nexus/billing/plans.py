"""Plans, credits and what each tier is allowed to route to.

Credits are the unit customers buy.  1 credit = $0.01 of retail value; the
platform charges `provider cost × markup` so gross margin is a config knob, not
a spreadsheet exercise.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nexus.config import settings
from nexus.db.models import PlanTier

CREDIT_USD = 0.01


@dataclass(frozen=True, slots=True)
class Plan:
    tier: PlanTier
    name: str
    price_usd_month: float
    monthly_credits: int
    routing_profiles: tuple[str, ...]
    default_profile: str
    max_episode_seconds: float
    max_concurrent_jobs: int
    max_shot_concurrency: int
    resolutions: tuple[str, ...]
    watermark: bool = False
    api_access: bool = False
    commercial_license: bool = False
    priority: int = 0
    features: tuple[str, ...] = field(default_factory=tuple)

    def allows_profile(self, profile: str) -> bool:
        return profile in self.routing_profiles

    def to_dict(self) -> dict:
        return {
            "tier": self.tier.value, "name": self.name,
            "price_usd_month": self.price_usd_month,
            "monthly_credits": self.monthly_credits,
            "included_usd": round(self.monthly_credits * CREDIT_USD, 2),
            "routing_profiles": list(self.routing_profiles),
            "default_profile": self.default_profile,
            "max_episode_minutes": round(self.max_episode_seconds / 60, 1),
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "resolutions": list(self.resolutions),
            "watermark": self.watermark,
            "api_access": self.api_access,
            "commercial_license": self.commercial_license,
            "features": list(self.features),
        }


PLANS: dict[PlanTier, Plan] = {
    PlanTier.FREE: Plan(
        tier=PlanTier.FREE, name="Free", price_usd_month=0.0, monthly_credits=300,
        routing_profiles=("offline", "free"), default_profile="free",
        max_episode_seconds=180, max_concurrent_jobs=1, max_shot_concurrency=2,
        resolutions=("480p", "720p"), watermark=True,
        features=("3-minute episodes", "free-tier models", "watermarked export",
                  "character bible + reference sheets"),
    ),
    PlanTier.STARTER: Plan(
        tier=PlanTier.STARTER, name="Starter", price_usd_month=39.0, monthly_credits=4_000,
        routing_profiles=("offline", "free", "budget", "balanced"), default_profile="budget",
        max_episode_seconds=480, max_concurrent_jobs=2, max_shot_concurrency=4,
        resolutions=("480p", "720p", "1080p"), api_access=True, commercial_license=True,
        priority=1,
        features=("8-minute episodes", "1080p", "no watermark", "API access",
                  "commercial licence"),
    ),
    PlanTier.STUDIO: Plan(
        tier=PlanTier.STUDIO, name="Studio", price_usd_month=149.0, monthly_credits=18_000,
        routing_profiles=("offline", "free", "budget", "balanced", "premium"),
        default_profile="balanced",
        max_episode_seconds=900, max_concurrent_jobs=4, max_shot_concurrency=6,
        resolutions=("480p", "720p", "1080p"), api_access=True, commercial_license=True,
        priority=2,
        features=("15-minute episodes", "premium models (Kling 3.0, Nano Banana Pro)",
                  "ElevenLabs voices", "4 concurrent productions", "series continuity across episodes"),
    ),
    PlanTier.SCALE: Plan(
        tier=PlanTier.SCALE, name="Scale", price_usd_month=549.0, monthly_credits=75_000,
        routing_profiles=("offline", "free", "budget", "balanced", "premium", "flagship"),
        default_profile="premium",
        max_episode_seconds=1200, max_concurrent_jobs=10, max_shot_concurrency=10,
        resolutions=("480p", "720p", "1080p", "1440p"), api_access=True,
        commercial_license=True, priority=3,
        features=("20-minute episodes", "flagship models (Veo 3.1, Seedance 2.5, Opus 5)",
                  "10 concurrent productions", "priority queue", "webhooks"),
    ),
    PlanTier.ENTERPRISE: Plan(
        tier=PlanTier.ENTERPRISE, name="Enterprise", price_usd_month=0.0, monthly_credits=0,
        routing_profiles=("offline", "free", "budget", "balanced", "premium", "flagship"),
        default_profile="premium",
        max_episode_seconds=3600, max_concurrent_jobs=50, max_shot_concurrency=24,
        resolutions=("480p", "720p", "1080p", "1440p", "4k"), api_access=True,
        commercial_license=True, priority=4,
        features=("custom runtimes", "bring your own API keys", "dedicated capacity", "SSO", "SLA"),
    ),
}


def get_plan(tier: PlanTier | str) -> Plan:
    if isinstance(tier, str):
        tier = PlanTier(tier)
    return PLANS[tier]


def stripe_price_id(tier: PlanTier) -> str | None:
    return {
        PlanTier.STARTER: settings.stripe_price_starter,
        PlanTier.STUDIO: settings.stripe_price_studio,
        PlanTier.SCALE: settings.stripe_price_scale,
    }.get(tier)


def tier_for_price_id(price_id: str) -> PlanTier | None:
    mapping = {
        settings.stripe_price_starter: PlanTier.STARTER,
        settings.stripe_price_studio: PlanTier.STUDIO,
        settings.stripe_price_scale: PlanTier.SCALE,
    }
    return mapping.get(price_id)


def usd_to_credits(usd: float) -> int:
    """Retail credits for a raw provider cost, rounded up so we never undercharge."""
    retail = usd * settings.credit_markup_multiplier
    return max(0, int(retail / CREDIT_USD + 0.999))


def credits_to_usd(credits: int) -> float:
    return round(credits * CREDIT_USD, 4)


def estimate_episode_cost_usd(target_seconds: float, profile: str) -> float:
    """Pre-flight estimate used for the budget reservation and the UI quote.

    Derived from the shot maths the pipeline actually uses: ~6.5s per shot, one
    keyframe and one clip each, plus dialogue, writing and a score.
    """
    shots = max(1, int(target_seconds / 6.5))
    per_shot = {
        "offline": (0.0, 0.0), "free": (0.0, 0.0),
        "budget": (0.003, 0.28), "balanced": (0.03, 0.45),
        "premium": (0.14, 1.15), "flagship": (0.14, 2.90),
    }.get(profile, (0.03, 0.45))
    keyframe_usd, clip_usd = per_shot
    writing_usd = {"offline": 0.0, "free": 0.0, "budget": 0.05,
                   "balanced": 0.55, "premium": 1.80, "flagship": 3.20}.get(profile, 0.55)
    dialogue_usd = {"offline": 0.0, "free": 0.0, "budget": 0.02,
                    "balanced": 0.09, "premium": 0.18, "flagship": 0.18}.get(profile, 0.09)
    words = target_seconds * 1.6
    audio_usd = (words * 5.5 / 1000) * dialogue_usd * 10
    score_usd = {"offline": 0.0, "free": 0.0, "budget": 0.6,
                 "balanced": 0.6, "premium": 2.4, "flagship": 2.4}.get(profile, 0.6)
    references_usd = keyframe_usd * 12
    return round(
        shots * (keyframe_usd + clip_usd) + writing_usd + audio_usd + score_usd + references_usd, 4
    )


def estimate_episode_credits(target_seconds: float, profile: str) -> int:
    return usd_to_credits(estimate_episode_cost_usd(target_seconds, profile))
