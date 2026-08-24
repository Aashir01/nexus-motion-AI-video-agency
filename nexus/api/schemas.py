"""Wire schemas for the public API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

# ── auth ──────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    organization_name: str = Field("", max_length=200)
    display_name: str = Field("", max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    org_id: str
    org_name: str
    plan: str
    credit_balance: int


class ApiKeyCreate(BaseModel):
    name: str = Field("default", max_length=120)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked: bool = False
    secret: str | None = Field(None, description="Returned exactly once, at creation")


# ── series & episodes ─────────────────────────────────────────────────────────

class SeriesCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=240)
    brief: str = Field(..., min_length=20, max_length=8000,
                       description="The creative brief the showrunner agent works from")
    genre: str = Field("drama", max_length=80)
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:5", "2.39:1"] = "16:9"
    language: str = Field("en", max_length=12)


class SeriesOut(BaseModel):
    id: str
    title: str
    brief: str
    logline: str
    genre: str
    aspect_ratio: str
    language: str
    episode_count: int
    has_bible: bool
    characters: list[dict] = Field(default_factory=list)
    created_at: datetime


class EpisodeCreate(BaseModel):
    premise: str = Field("", max_length=4000)
    number: int | None = Field(None, ge=1, le=999)
    target_minutes: float = Field(12.0, ge=0.5, le=60.0)


class EpisodeOut(BaseModel):
    id: str
    series_id: str
    number: int
    title: str
    premise: str
    status: str
    target_seconds: float
    duration_seconds: float | None
    shot_count: int
    video_url: str | None
    thumbnail_url: str | None
    subtitle_url: str | None
    cost_usd: float
    created_at: datetime


# ── jobs ──────────────────────────────────────────────────────────────────────

class ProductionCreate(BaseModel):
    series_id: str
    episode_id: str | None = None
    premise: str = Field("", max_length=4000)
    target_minutes: float = Field(12.0, ge=0.5, le=60.0)
    profile: Literal["offline", "free", "budget", "balanced", "premium", "flagship"] = "balanced"
    resolution: Literal["480p", "720p", "1080p", "1440p", "4k"] = "1080p"
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:5", "2.39:1"] | None = None
    burn_subtitles: bool = False
    subtitles: bool = True
    generate_score: bool = True
    critique: bool = True
    continuity_audit: bool = True
    reference_images_per_character: int = Field(3, ge=1, le=4)
    budget_usd: float | None = Field(None, gt=0, le=5000)
    model_overrides: dict[str, str] | None = Field(
        None, description="role → model id, e.g. {'shot_video': 'fal/kling-3.0-pro'}"
    )
    seed: int | None = None

    @field_validator("model_overrides")
    @classmethod
    def _known_models(cls, value):
        if not value:
            return value
        from nexus.routing.catalog import CATALOG

        unknown = [m for m in value.values() if m not in CATALOG]
        if unknown:
            raise ValueError(f"unknown model id(s): {', '.join(unknown)}")
        return value


class JobOut(BaseModel):
    id: str
    org_id: str
    episode_id: str | None
    status: str
    stage: str
    progress_pct: float
    message: str
    cost_usd: float
    credits_reserved: int
    credits_charged: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict[str, Any] | None = None


class QuoteRequest(BaseModel):
    target_minutes: float = Field(12.0, ge=0.5, le=60.0)
    profile: Literal["offline", "free", "budget", "balanced", "premium", "flagship"] = "balanced"


class QuoteResponse(BaseModel):
    target_minutes: float
    profile: str
    estimated_shots: int
    estimated_cost_usd: float
    estimated_credits: int
    credit_balance: int
    affordable: bool
    routing_plan: dict[str, list[str]]


# ── billing ───────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    tier: Literal["starter", "studio", "scale"]


class TopUpRequest(BaseModel):
    credits: int = Field(..., ge=1000, le=1_000_000)


class CheckoutResponse(BaseModel):
    checkout_url: str


class CreditEntryOut(BaseModel):
    id: str
    delta: int
    balance_after: int
    reason: str
    note: str
    job_id: str | None
    created_at: datetime


class UsageSummary(BaseModel):
    period_days: int
    total_cost_usd: float
    total_calls: int
    by_model: dict[str, float]
    by_role: dict[str, float]
    fallback_rate: float
