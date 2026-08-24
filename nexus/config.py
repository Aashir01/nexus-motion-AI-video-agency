"""Central configuration for the Nexus Motion platform.

Every knob is environment driven so the same image runs locally, in CI and in
production.  Nothing in here reaches out to the network at import time.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "staging", "production", "test"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Core ───────────────────────────────────────────────────────────
    environment: Environment = "local"
    app_name: str = "Nexus Motion"
    public_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"
    secret_key: str = Field(
        default="dev-insecure-secret-change-me",
        description="Signs JWTs and API-key hashes. MUST be overridden in production.",
    )
    access_token_ttl_minutes: int = 60 * 12
    refresh_token_ttl_days: int = 30
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── Datastores ────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./nexus.db"
    redis_url: str = "redis://localhost:6379/0"
    db_echo: bool = False

    # ── Storage ──────────────────────────────────────────────────────
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = "./storage"
    s3_bucket: str = "nexus-motion"
    s3_endpoint_url: str | None = None          # set for R2 / MinIO
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_public_base_url: str | None = None       # CDN in front of the bucket

    # ── Rendering ────────────────────────────────────────────────────
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    render_fps: int = 24
    render_preset: str = "medium"
    render_crf: int = 20

    # ── Pipeline limits ───────────────────────────────────────────────
    max_parallel_shot_jobs: int = 6
    shot_generation_timeout_s: int = 900
    llm_timeout_s: int = 180
    http_max_retries: int = 4
    default_target_minutes: float = 12.0

    # ── Billing ──────────────────────────────────────────────────────
    billing_enabled: bool = False
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_starter: str | None = None
    stripe_price_studio: str | None = None
    stripe_price_scale: str | None = None
    credit_markup_multiplier: float = Field(
        default=1.6,
        description="Retail credit price relative to raw provider cost.",
    )

    # ── Model routing ─────────────────────────────────────────────────
    routing_profile_default: str = "balanced"
    allow_paid_models_without_billing: bool = True
    provider_health_window_s: int = 300
    provider_failure_threshold: int = 3

    # ── Provider credentials (all optional; the router adapts) ────────────
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    groq_api_key: str | None = None
    deepseek_api_key: str | None = None
    together_api_key: str | None = None
    fireworks_api_key: str | None = None
    mistral_api_key: str | None = None
    xai_api_key: str | None = None
    cerebras_api_key: str | None = None
    ollama_base_url: str | None = None

    fal_api_key: str | None = None
    replicate_api_token: str | None = None
    runway_api_key: str | None = None
    luma_api_key: str | None = None
    kling_access_key: str | None = None
    kling_secret_key: str | None = None
    minimax_api_key: str | None = None
    huggingface_api_key: str | None = None

    elevenlabs_api_key: str | None = None
    cartesia_api_key: str | None = None
    deepgram_api_key: str | None = None

    # ── Observability ─────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    sentry_dsn: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic and other sync tooling need a non-async driver."""
        return (
            self.database_url
            .replace("+asyncpg", "+psycopg")
            .replace("+aiosqlite", "")
        )

    def provider_key(self, provider: str) -> str | None:
        """Look up a provider credential by canonical provider id."""
        return getattr(self, f"{provider}_api_key", None) or getattr(
            self, f"{provider}_api_token", None
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def reload_settings() -> Settings:
    """Test helper — clears the cache after env mutation."""
    get_settings.cache_clear()
    globals()["settings"] = get_settings()
    return globals()["settings"]


os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
