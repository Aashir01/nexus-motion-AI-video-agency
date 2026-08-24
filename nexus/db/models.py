"""Relational model for the SaaS.

Multi-tenant by organisation: every row that matters hangs off `org_id`, and the
API layer never issues a query without it.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from nexus.db.base import Base, TimestampMixin
from nexus.util.ids import new_id


class PlanTier(str, enum.Enum):
    FREE = "free"
    STARTER = "starter"
    STUDIO = "studio"
    SCALE = "scale"
    ENTERPRISE = "enterprise"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class EpisodeStatus(str, enum.Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("org"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    plan: Mapped[PlanTier] = mapped_column(Enum(PlanTier, native_enum=False), default=PlanTier.FREE)
    credit_balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    monthly_credit_grant: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(80), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(80))
    subscription_status: Mapped[str | None] = mapped_column(String(40))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    users: Mapped[list[User]] = relationship(back_populates="org", cascade="all, delete-orphan")
    series: Mapped[list[Series]] = relationship(back_populates="org", cascade="all, delete-orphan")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False), default=UserRole.OWNER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    org: Mapped[Organization] = relationship(back_populates="users")


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("key"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120), default="default")
    prefix: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Series(Base, TimestampMixin):
    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("srs"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    brief: Mapped[str] = mapped_column(Text, default="")
    logline: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(80), default="drama")
    aspect_ratio: Mapped[str] = mapped_column(String(12), default="16:9")
    language: Mapped[str] = mapped_column(String(12), default="en")
    bible: Mapped[dict | None] = mapped_column(JSON)
    episode_counter: Mapped[int] = mapped_column(Integer, default=0)

    org: Mapped[Organization] = relationship(back_populates="series")
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )


class Episode(Base, TimestampMixin):
    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("series_id", "number", name="uq_episodes_series_id_number"),
        Index("ix_episodes_org_status", "org_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ep"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(240), default="")
    premise: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[EpisodeStatus] = mapped_column(
        Enum(EpisodeStatus, native_enum=False), default=EpisodeStatus.DRAFT
    )
    target_seconds: Mapped[float] = mapped_column(Float, default=720.0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    shot_count: Mapped[int] = mapped_column(Integer, default=0)
    video_key: Mapped[str | None] = mapped_column(String(400))
    video_url: Mapped[str | None] = mapped_column(String(800))
    thumbnail_key: Mapped[str | None] = mapped_column(String(400))
    subtitle_key: Mapped[str | None] = mapped_column(String(400))
    plan_key: Mapped[str | None] = mapped_column(String(400))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    series: Mapped[Series] = relationship(back_populates="episodes")
    jobs: Mapped[list[Job]] = relationship(back_populates="episode", cascade="all, delete-orphan")


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_org_status_created", "org_id", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.QUEUED, index=True
    )
    stage: Mapped[str] = mapped_column(String(60), default="")
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    credits_reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    credits_charged: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(80))

    episode: Mapped[Episode | None] = relationship(back_populates="jobs")


class CreditEntry(Base, TimestampMixin):
    __tablename__ = "credit_entries"
    __table_args__ = (Index("ix_credit_entries_org_created", "org_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cre"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(40), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    external_ref: Mapped[str | None] = mapped_column(String(120), index=True)


class UsageRecord(Base, TimestampMixin):
    """One row per model call — the audit trail behind every credit charged."""

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_org_created", "org_id", "created_at"),)

    # SQLite only autoincrements INTEGER PRIMARY KEY, never BIGINT — the
    # variant keeps 64-bit ids on Postgres without breaking local dev.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    org_id: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(40), default="")
    model_id: Mapped[str] = mapped_column(String(120), default="")
    provider: Mapped[str] = mapped_column(String(60), default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    used_fallback: Mapped[bool] = mapped_column(Boolean, default=False)


class WebhookEvent(Base, TimestampMixin):
    """Stripe delivers at-least-once; this table makes handling exactly-once."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), default="stripe")
    event_type: Mapped[str] = mapped_column(String(120), default="")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSON)
