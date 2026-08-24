"""Executes one queued production job against the database."""
from __future__ import annotations

import asyncio
import json
import os
import socket

from nexus.billing import credits
from nexus.billing.plans import get_plan
from nexus.db.models import (
    Episode,
    EpisodeStatus,
    Job,
    JobStatus,
    Organization,
    Series,
    UsageRecord,
)
from nexus.db.session import session_scope
from nexus.pipeline.context import ProductionOptions, ProgressEvent
from nexus.pipeline.runner import ProductionRequest, build_context, produce
from nexus.story.schemas import SeriesBible
from nexus.util.errors import NexusError
from nexus.util.logging import bind, get_logger, unbind
from nexus.worker.bus import bus

log = get_logger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def execute_job(job_id: str) -> None:
    token = bind(job_id=job_id, worker=WORKER_ID)
    try:
        await _execute(job_id)
    finally:
        unbind(token)


async def _execute(job_id: str) -> None:
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None:
            log.warning("job_missing")
            return
        if job.status.terminal:
            log.info("job_already_finished", extra={"status": job.status.value})
            return

        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.worker_id = WORKER_ID
        job.started_at = job.started_at or _now()
        episode = await session.get(Episode, job.episode_id) if job.episode_id else None
        series = await session.get(Series, episode.series_id) if episode else None
        org = await session.get(Organization, job.org_id)
        if episode:
            episode.status = EpisodeStatus.GENERATING
        options_payload = dict(job.options or {})
        reserved = job.credits_reserved
        bible_payload = series.bible if series else None
        premise = episode.premise if episode else ""
        brief = series.brief if series else options_payload.get("brief", "")
        episode_number = episode.number if episode else 1
        recap = options_payload.pop("recap", "")
        plan = get_plan(org.plan) if org else get_plan("free")

    await bus.publish(job_id, {"stage": "pipeline", "status": "queued_pickup",
                               "message": "worker picked up the job", "overall_pct": 0.0})

    options = _options_from_payload(options_payload, plan)
    bible = SeriesBible.model_validate(bible_payload) if bible_payload else None

    request = ProductionRequest(
        brief=brief, premise=premise, episode_number=episode_number, recap=recap,
        options=options, budget_usd=options_payload.get("budget_usd"),
        model_overrides=options_payload.get("model_overrides"),
        bible=bible, job_id=job_id,
    )

    ctx = build_context(request)
    saved = await ctx.load_checkpoint()
    if saved:
        ctx.plan = saved
        log.info("resuming_from_checkpoint", extra={"shots": saved.episode.shot_count()})

    cancel_watch = asyncio.create_task(_watch_cancellation(job_id, ctx))
    ctx.progress_hook = _make_hook(job_id)

    try:
        summary = await produce(request, context=ctx)
    except asyncio.CancelledError:
        await _finalise(job_id, JobStatus.CANCELLED, ctx, reserved, error="cancelled by request")
        raise
    except NexusError as exc:
        log.error("job_failed", extra={"code": exc.code, "error": str(exc)[:400]})
        await _finalise(job_id, JobStatus.FAILED, ctx, reserved, error=str(exc))
        return
    except Exception as exc:
        log.exception("job_crashed")
        await _finalise(job_id, JobStatus.FAILED, ctx, reserved, error=f"{type(exc).__name__}: {exc}")
        return
    finally:
        cancel_watch.cancel()
        await bus.clear_cancel(job_id)

    await _finalise(job_id, JobStatus.SUCCEEDED, ctx, reserved, summary=summary)


def _options_from_payload(payload: dict, plan) -> ProductionOptions:
    options = ProductionOptions()
    for field in ProductionOptions.__dataclass_fields__:  # type: ignore[attr-defined]
        if field in payload and payload[field] is not None:
            setattr(options, field, payload[field])
    # Plan limits are enforced here as well as at the API boundary — a job row
    # can outlive a downgrade.
    options.target_seconds = min(options.target_seconds, plan.max_episode_seconds)
    options.max_shot_concurrency = min(
        options.shot_concurrency or plan.max_shot_concurrency, plan.max_shot_concurrency
    )
    if not plan.allows_profile(options.profile):
        options.profile = plan.default_profile
    if options.resolution not in plan.resolutions:
        options.resolution = plan.resolutions[-1]
    return options


def _make_hook(job_id: str):
    last_write = {"pct": -5.0}

    async def hook(event: ProgressEvent) -> None:
        payload = event.to_dict()
        await bus.publish(job_id, payload)
        # Persist sparingly: the bus carries the live feed, the row only needs
        # to be good enough for a page refresh.
        if event.status in ("started", "completed", "failed") or \
                event.overall_pct - last_write["pct"] >= 4.0:
            last_write["pct"] = event.overall_pct
            async with session_scope() as session:
                job = await session.get(Job, job_id)
                if job and not job.status.terminal:
                    job.stage = event.stage
                    job.progress_pct = round(event.overall_pct, 2)
                    job.message = event.message[:500]

    return hook


async def _watch_cancellation(job_id: str, ctx) -> None:
    try:
        while True:
            await asyncio.sleep(5)
            if await bus.is_cancelled(job_id):
                log.info("cancellation_requested")
                ctx.cancel()
                return
    except asyncio.CancelledError:
        return


async def _finalise(
    job_id: str, status: JobStatus, ctx, reserved: int,
    *, summary: dict | None = None, error: str | None = None,
) -> None:
    ledger = ctx.router.ledger
    actual_usd = ledger.spent_usd

    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = _now()
        job.cost_usd = round(actual_usd, 6)
        job.error = (error or None) and error[:4000]
        job.progress_pct = 100.0 if status is JobStatus.SUCCEEDED else job.progress_pct
        job.result = summary if summary else job.result
        job.message = (error or "production complete")[:500]

        for entry in ledger.entries:
            session.add(UsageRecord(
                org_id=job.org_id, job_id=job_id, role=entry.role, model_id=entry.model_id,
                provider=entry.provider, cost_usd=entry.cost_usd, latency_ms=entry.latency_ms,
                used_fallback=len(entry.fallback_chain) > 1,
            ))

        if status is JobStatus.SUCCEEDED:
            charged, _ = await credits.settle(
                session, job.org_id, job_id, reserved=reserved, actual_usd=actual_usd
            )
            job.credits_charged = charged
        else:
            await credits.refund_reservation(session, job.org_id, job_id, reserved)
            job.credits_charged = 0

        episode = await session.get(Episode, job.episode_id) if job.episode_id else None
        if episode:
            ep = ctx.episode
            episode.status = (
                EpisodeStatus.READY if status is JobStatus.SUCCEEDED else EpisodeStatus.FAILED
            )
            episode.title = ep.title or episode.title
            episode.duration_seconds = ep.actual_seconds
            episode.shot_count = ep.shot_count()
            episode.video_key = ep.final_video_asset_key
            episode.video_url = ep.final_video_url
            episode.thumbnail_key = ep.thumbnail_asset_key
            episode.subtitle_key = ep.subtitle_asset_key
            episode.plan_key = ctx.key("plan.json")
            episode.cost_usd = round(actual_usd, 6)

            series = await session.get(Series, episode.series_id)
            if series and ctx.bible.characters:
                # Lock the bible after the first episode so later episodes reuse
                # the same cast, wardrobe and reference portraits.
                series.bible = json.loads(ctx.bible.model_dump_json())
                series.title = series.title or ctx.bible.title
                series.logline = series.logline or ctx.bible.logline

    await bus.publish(job_id, {
        "stage": "pipeline", "status": status.value,
        "message": error or "production complete", "overall_pct": 100.0,
        "detail": {"cost_usd": round(actual_usd, 4),
                   "video_url": ctx.episode.final_video_url},
    })


def _now():
    from nexus.db.base import utcnow

    return utcnow()
