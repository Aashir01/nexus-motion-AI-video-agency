"""Everything a production run carries with it."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nexus.config import settings
from nexus.routing.router import ModelRouter
from nexus.storage import Storage, asset_key, get_storage
from nexus.story.schemas import ProductionPlan
from nexus.util.ids import new_id
from nexus.util.logging import get_logger

log = get_logger(__name__)

ProgressHook = Callable[["ProgressEvent"], Awaitable[None] | None]


@dataclass(slots=True)
class ProgressEvent:
    job_id: str
    stage: str
    status: str            # started | progress | completed | failed | skipped
    message: str = ""
    completed: int = 0
    total: int = 0
    overall_pct: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "stage": self.stage, "status": self.status,
            "message": self.message, "completed": self.completed, "total": self.total,
            "overall_pct": round(self.overall_pct, 2), "detail": self.detail, "ts": self.ts,
        }


@dataclass(slots=True)
class ProductionOptions:
    target_seconds: float = 720.0
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    language: str = "en"
    content_rating: str = "PG-13"
    profile: str = "balanced"
    reference_images_per_character: int = 3
    generate_location_plates: bool = True
    generate_score: bool = True
    generate_ambience: bool = False
    burn_subtitles: bool = False
    subtitles: bool = True
    critique: bool = True
    continuity_audit: bool = True
    max_shot_concurrency: int = 0        # 0 → settings.max_parallel_shot_jobs
    regenerate_flagged_shots: bool = False
    seed: int | None = None

    @property
    def shot_concurrency(self) -> int:
        return self.max_shot_concurrency or settings.max_parallel_shot_jobs


@dataclass
class ProductionContext:
    job_id: str
    plan: ProductionPlan
    router: ModelRouter
    options: ProductionOptions
    storage: Storage = field(default_factory=get_storage)
    progress_hook: ProgressHook | None = None
    workspace: str = ""
    stage_history: list[dict] = field(default_factory=list)
    _stage_weights: dict[str, float] = field(default_factory=dict)
    _completed_weight: float = 0.0
    _current_stage: str = ""
    _cancelled: bool = False

    def __post_init__(self) -> None:
        self.workspace = self.workspace or asset_key("jobs", self.job_id)

    # ── assets ───────────────────────────────────────────────────────────

    def key(self, *parts: str) -> str:
        return asset_key(self.workspace, *parts)

    async def put_bytes(self, rel_key: str, data: bytes, content_type: str | None = None):
        return await self.storage.put_bytes(self.key(rel_key), data, content_type)

    async def checkpoint(self) -> None:
        """Persist the plan so a crashed or evicted worker can resume mid-episode."""
        payload = self.plan.model_dump_json(indent=None).encode("utf-8")
        await self.storage.put_bytes(self.key("plan.json"), payload, "application/json")

    async def load_checkpoint(self) -> ProductionPlan | None:
        try:
            raw = await self.storage.get_bytes(self.key("plan.json"))
        except Exception:
            return None
        return ProductionPlan.model_validate(json.loads(raw))

    # ── progress ─────────────────────────────────────────────────────────

    def register_stages(self, weights: dict[str, float]) -> None:
        self._stage_weights = weights

    async def emit(self, status: str, message: str = "", *, stage: str | None = None,
                   completed: int = 0, total: int = 0, **detail) -> None:
        stage = stage or self._current_stage
        weight = self._stage_weights.get(stage, 0.0)
        fraction = (completed / total) if total else (1.0 if status == "completed" else 0.0)
        total_weight = sum(self._stage_weights.values()) or 1.0
        pct = 100.0 * (self._completed_weight + weight * fraction) / total_weight

        event = ProgressEvent(
            job_id=self.job_id, stage=stage, status=status, message=message,
            completed=completed, total=total, overall_pct=min(pct, 100.0), detail=detail,
        )
        payload = event.to_dict()
        payload["note"] = payload.pop("message")   # 'message' is reserved on LogRecord
        log.info("pipeline_progress", extra=payload)
        if self.progress_hook:
            result = self.progress_hook(event)
            if asyncio.iscoroutine(result):
                await result

    def begin_stage(self, stage: str) -> None:
        self._current_stage = stage

    def finish_stage(self, stage: str) -> None:
        self._completed_weight += self._stage_weights.get(stage, 0.0)

    # ── control ──────────────────────────────────────────────────────────

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError("production cancelled")

    # ── convenience ──────────────────────────────────────────────────────

    @property
    def bible(self):
        return self.plan.bible

    @property
    def episode(self):
        return self.plan.episode

    def warn(self, message: str) -> None:
        log.warning("pipeline_warning", extra={"job_id": self.job_id, "warning": message})
        self.plan.warnings.append(message)

    def summary(self) -> dict:
        return {
            "job_id": self.job_id,
            "title": self.episode.title,
            "scenes": len(self.episode.scenes),
            "shots": self.episode.shot_count(),
            "planned_seconds": round(self.episode.planned_seconds(), 1),
            "actual_seconds": self.episode.actual_seconds,
            "video_url": self.episode.final_video_url,
            "cost": self.router.ledger.summary(),
            "routes": [d.describe() for d in self.router.decisions[:60]],
            "warnings": self.plan.warnings,
            "stages": self.stage_history,
        }


def new_job_id() -> str:
    return new_id("job")
