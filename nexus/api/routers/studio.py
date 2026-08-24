"""Series, episodes and the production endpoints — the core of the product."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.api.deps import Principal, get_principal, owned_or_404
from nexus.api.schemas import (
    EpisodeCreate,
    EpisodeOut,
    JobOut,
    ProductionCreate,
    QuoteRequest,
    QuoteResponse,
    SeriesCreate,
    SeriesOut,
)
from nexus.billing import credits
from nexus.billing.plans import estimate_episode_cost_usd, estimate_episode_credits
from nexus.db.models import Episode, EpisodeStatus, Job, JobStatus, Series
from nexus.db.session import get_session
from nexus.routing.router import build_router
from nexus.storage import get_storage
from nexus.util.errors import (
    ConflictError,
    ForbiddenError,
    InsufficientCreditsError,
    ValidationError,
)
from nexus.worker.bus import bus

router = APIRouter(tags=["studio"])

# Statuses that end an SSE stream. The orchestrator's own "completed" is not
# one of them: the job is finished when the worker has settled credits and
# written the episode row, which it announces separately.
_TERMINAL_EVENTS = {"succeeded", "failed", "cancelled"}


# ── series ────────────────────────────────────────────────────────────────────

@router.post("/series", response_model=SeriesOut, status_code=201)
async def create_series(payload: SeriesCreate, principal: Principal = Depends(get_principal),
                        session: AsyncSession = Depends(get_session)):
    series = Series(
        org_id=principal.org_id, title=payload.title, brief=payload.brief,
        genre=payload.genre, aspect_ratio=payload.aspect_ratio, language=payload.language,
    )
    session.add(series)
    await session.commit()
    return _series_out(series, 0)


@router.get("/series", response_model=list[SeriesOut])
async def list_series(principal: Principal = Depends(get_principal),
                      session: AsyncSession = Depends(get_session),
                      limit: int = Query(50, ge=1, le=200)):
    rows = (await session.execute(
        select(Series).where(Series.org_id == principal.org_id)
        .order_by(Series.created_at.desc()).limit(limit)
    )).scalars().all()
    counts = dict((await session.execute(
        select(Episode.series_id, func.count(Episode.id))
        .where(Episode.org_id == principal.org_id).group_by(Episode.series_id)
    )).all())
    return [_series_out(s, counts.get(s.id, 0)) for s in rows]


@router.get("/series/{series_id}", response_model=SeriesOut)
async def get_series(series_id: str, principal: Principal = Depends(get_principal),
                     session: AsyncSession = Depends(get_session)):
    series = await owned_or_404(session, Series, series_id, principal.org_id)
    count = (await session.execute(
        select(func.count(Episode.id)).where(Episode.series_id == series_id)
    )).scalar_one()
    return _series_out(series, count)


@router.get("/series/{series_id}/bible")
async def get_bible(series_id: str, principal: Principal = Depends(get_principal),
                    session: AsyncSession = Depends(get_session)):
    """The show bible, including every character's reference portraits."""
    series = await owned_or_404(session, Series, series_id, principal.org_id)
    if not series.bible:
        return {"status": "not_generated",
                "message": "the bible is written during the first production run"}
    return series.bible


@router.delete("/series/{series_id}", status_code=204)
async def delete_series(series_id: str, principal: Principal = Depends(get_principal),
                        session: AsyncSession = Depends(get_session)):
    series = await owned_or_404(session, Series, series_id, principal.org_id)
    running = (await session.execute(
        select(func.count(Job.id)).join(Episode, Job.episode_id == Episode.id)
        .where(Episode.series_id == series_id,
               Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
    )).scalar_one()
    if running:
        raise ConflictError(f"{running} production(s) are still running for this series")
    await session.delete(series)
    await session.commit()


# ── episodes ──────────────────────────────────────────────────────────────────

@router.post("/series/{series_id}/episodes", response_model=EpisodeOut, status_code=201)
async def create_episode(series_id: str, payload: EpisodeCreate,
                         principal: Principal = Depends(get_principal),
                         session: AsyncSession = Depends(get_session)):
    series = await owned_or_404(session, Series, series_id, principal.org_id)
    target_seconds = _clamp_runtime(payload.target_minutes * 60, principal)

    number = payload.number
    if number is None:
        highest = (await session.execute(
            select(func.max(Episode.number)).where(Episode.series_id == series_id)
        )).scalar_one()
        number = (highest or 0) + 1

    episode = Episode(
        org_id=principal.org_id, series_id=series.id, number=number,
        premise=payload.premise, target_seconds=target_seconds,
    )
    session.add(episode)
    series.episode_counter = max(series.episode_counter, number)
    await session.commit()
    return _episode_out(episode)


@router.get("/series/{series_id}/episodes", response_model=list[EpisodeOut])
async def list_episodes(series_id: str, principal: Principal = Depends(get_principal),
                        session: AsyncSession = Depends(get_session)):
    await owned_or_404(session, Series, series_id, principal.org_id)
    rows = (await session.execute(
        select(Episode).where(Episode.series_id == series_id).order_by(Episode.number)
    )).scalars().all()
    return [_episode_out(e) for e in rows]


@router.get("/episodes/{episode_id}", response_model=EpisodeOut)
async def get_episode(episode_id: str, principal: Principal = Depends(get_principal),
                      session: AsyncSession = Depends(get_session)):
    episode = await owned_or_404(session, Episode, episode_id, principal.org_id)
    return _episode_out(episode)


@router.get("/episodes/{episode_id}/plan")
async def get_episode_plan(episode_id: str, principal: Principal = Depends(get_principal),
                           session: AsyncSession = Depends(get_session)):
    """The full production plan: scenes, shots, prompts, per-shot assets."""
    episode = await owned_or_404(session, Episode, episode_id, principal.org_id)
    if not episode.plan_key:
        return {"status": "not_generated"}
    raw = await get_storage().get_bytes(episode.plan_key)
    return json.loads(raw)


# ── productions ───────────────────────────────────────────────────────────────

@router.post("/productions/quote", response_model=QuoteResponse)
async def quote(payload: QuoteRequest, principal: Principal = Depends(get_principal)):
    """What an episode will cost, and which models will actually serve it."""
    if not principal.plan.allows_profile(payload.profile):
        raise ForbiddenError(
            f"the {principal.plan.name} plan cannot use the {payload.profile!r} profile; "
            f"available: {', '.join(principal.plan.routing_profiles)}"
        )
    seconds = _clamp_runtime(payload.target_minutes * 60, principal)
    estimated_credits = estimate_episode_credits(seconds, payload.profile)
    model_router = build_router(profile=payload.profile)
    return QuoteResponse(
        target_minutes=round(seconds / 60, 2),
        profile=payload.profile,
        estimated_shots=max(1, int(seconds / 6.5)),
        estimated_cost_usd=estimate_episode_cost_usd(seconds, payload.profile),
        estimated_credits=estimated_credits,
        credit_balance=principal.org.credit_balance,
        affordable=principal.org.credit_balance >= estimated_credits,
        routing_plan=model_router.plan(),
    )


@router.post("/productions", response_model=JobOut, status_code=202)
async def start_production(payload: ProductionCreate,
                           principal: Principal = Depends(get_principal),
                           session: AsyncSession = Depends(get_session)):
    plan = principal.plan
    if not plan.allows_profile(payload.profile):
        raise ForbiddenError(
            f"the {plan.name} plan cannot use the {payload.profile!r} profile; "
            f"upgrade or choose one of: {', '.join(plan.routing_profiles)}"
        )
    if payload.resolution not in plan.resolutions:
        raise ForbiddenError(
            f"{payload.resolution} is not available on the {plan.name} plan "
            f"({', '.join(plan.resolutions)})"
        )

    series = await owned_or_404(session, Series, payload.series_id, principal.org_id)

    active = (await session.execute(
        select(func.count(Job.id)).where(
            Job.org_id == principal.org_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    )).scalar_one()
    if active >= plan.max_concurrent_jobs:
        raise ConflictError(
            f"{active} production(s) already running; the {plan.name} plan allows "
            f"{plan.max_concurrent_jobs} at once"
        )

    target_seconds = _clamp_runtime(payload.target_minutes * 60, principal)

    if payload.episode_id:
        episode = await owned_or_404(session, Episode, payload.episode_id, principal.org_id)
        if episode.status is EpisodeStatus.GENERATING:
            raise ConflictError("this episode is already being produced")
        episode.target_seconds = target_seconds
        if payload.premise:
            episode.premise = payload.premise
    else:
        highest = (await session.execute(
            select(func.max(Episode.number)).where(Episode.series_id == series.id)
        )).scalar_one()
        episode = Episode(
            org_id=principal.org_id, series_id=series.id, number=(highest or 0) + 1,
            premise=payload.premise, target_seconds=target_seconds,
        )
        session.add(episode)
        await session.flush()

    required = estimate_episode_credits(target_seconds, payload.profile)
    if principal.org.credit_balance < required:
        raise InsufficientCreditsError(
            f"this production is quoted at {required:,} credits but the balance is "
            f"{principal.org.credit_balance:,}",
            detail={"required": required, "balance": principal.org.credit_balance,
                    "top_up_url": "/billing/top-up"},
        )

    recap = await _previous_recap(session, series.id, episode.number)
    job = Job(
        org_id=principal.org_id, episode_id=episode.id,
        created_by=principal.user.id if principal.user else None,
        status=JobStatus.QUEUED, stage="queued",
        credits_reserved=required,
        options={
            "brief": series.brief,
            "recap": recap,
            "target_seconds": target_seconds,
            "profile": payload.profile,
            "resolution": payload.resolution,
            "aspect_ratio": payload.aspect_ratio or series.aspect_ratio,
            "language": series.language,
            "burn_subtitles": payload.burn_subtitles,
            "subtitles": payload.subtitles,
            "generate_score": payload.generate_score,
            "critique": payload.critique,
            "continuity_audit": payload.continuity_audit,
            "reference_images_per_character": payload.reference_images_per_character,
            "max_shot_concurrency": plan.max_shot_concurrency,
            "budget_usd": payload.budget_usd,
            "model_overrides": payload.model_overrides,
            "seed": payload.seed,
        },
    )
    session.add(job)
    await credits.reserve(session, principal.org_id, required, job.id)
    episode.status = EpisodeStatus.GENERATING
    await session.commit()

    await bus.enqueue(job.id, priority=plan.priority >= 3)
    return _job_out(job)


@router.get("/productions", response_model=list[JobOut])
async def list_jobs(principal: Principal = Depends(get_principal),
                    session: AsyncSession = Depends(get_session),
                    status: str | None = None, limit: int = Query(30, ge=1, le=100)):
    stmt = select(Job).where(Job.org_id == principal.org_id)
    if status:
        stmt = stmt.where(Job.status == JobStatus(status))
    rows = (await session.execute(
        stmt.order_by(Job.created_at.desc()).limit(limit)
    )).scalars().all()
    return [_job_out(j) for j in rows]


@router.get("/productions/{job_id}", response_model=JobOut)
async def get_job(job_id: str, principal: Principal = Depends(get_principal),
                  session: AsyncSession = Depends(get_session)):
    job = await owned_or_404(session, Job, job_id, principal.org_id)
    return _job_out(job, include_result=True)


@router.post("/productions/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, principal: Principal = Depends(get_principal),
                     session: AsyncSession = Depends(get_session)):
    job = await owned_or_404(session, Job, job_id, principal.org_id)
    if job.status.terminal:
        raise ConflictError(f"this job already finished ({job.status.value})")
    await bus.request_cancel(job_id)
    if job.status is JobStatus.QUEUED:
        # Nothing has picked it up, so we can settle it here and now.
        job.status = JobStatus.CANCELLED
        job.message = "cancelled before it started"
        await credits.refund_reservation(session, principal.org_id, job_id, job.credits_reserved)
        await session.commit()
    return _job_out(job)


@router.get("/productions/{job_id}/events")
async def stream_events(job_id: str, request: Request,
                        principal: Principal = Depends(get_principal),
                        session: AsyncSession = Depends(get_session)):
    """Server-sent progress. Replays history first, so a reconnecting browser
    never sees a half-built timeline."""
    job = await owned_or_404(session, Job, job_id, principal.org_id)
    terminal = job.status.terminal

    async def generator():
        for event in await bus.replay(job_id):
            yield _sse(event)
        if terminal:
            yield _sse({"stage": "pipeline", "status": job.status.value,
                        "message": job.message, "overall_pct": job.progress_pct})
            yield "event: end\ndata: {}\n\n"
            return

        # The subscription is pumped into a queue rather than awaited directly:
        # cancelling a `wait_for` around an async generator's __anext__ leaves
        # that generator unusable, which silently truncates the stream.
        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in bus.subscribe(job_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                await queue.put(None)

        pump_task = asyncio.create_task(pump())
        idle = 0.0
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    idle += 15.0
                    yield ": keepalive\n\n"
                    if idle > 3600:
                        break
                    continue

                if event is None:
                    break
                idle = 0.0
                yield _sse(event)
                if event.get("stage") == "pipeline" and event.get("status") in _TERMINAL_EVENTS:
                    yield "event: end\ndata: {}\n\n"
                    break
        finally:
            pump_task.cancel()

    return StreamingResponse(
        generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _clamp_runtime(seconds: float, principal: Principal) -> float:
    limit = principal.plan.max_episode_seconds
    if seconds > limit:
        raise ValidationError(
            f"the {principal.plan.name} plan caps episodes at {limit / 60:.0f} minutes "
            f"(requested {seconds / 60:.1f})",
            detail={"max_minutes": limit / 60},
        )
    return max(30.0, seconds)


async def _previous_recap(session: AsyncSession, series_id: str, number: int) -> str:
    if number <= 1:
        return ""
    previous = (await session.execute(
        select(Episode).where(Episode.series_id == series_id, Episode.number < number)
        .order_by(Episode.number.desc()).limit(1)
    )).scalar_one_or_none()
    if not previous or not previous.title:
        return ""
    return f"Episode {previous.number}, '{previous.title}': {previous.premise}"


def _series_out(series: Series, episode_count: int) -> SeriesOut:
    bible = series.bible or {}
    return SeriesOut(
        id=series.id, title=series.title, brief=series.brief,
        logline=series.logline or bible.get("logline", ""), genre=series.genre,
        aspect_ratio=series.aspect_ratio, language=series.language,
        episode_count=episode_count, has_bible=bool(series.bible),
        characters=[
            {"id": c.get("id"), "name": c.get("name"), "role": c.get("role"),
             "one_line": c.get("one_line"),
             "portrait": next((r.get("url") for r in c.get("reference_images", [])
                               if r.get("is_canonical")), None)}
            for c in bible.get("characters", [])
        ],
        created_at=series.created_at,
    )


def _episode_out(episode: Episode) -> EpisodeOut:
    storage = get_storage()
    return EpisodeOut(
        id=episode.id, series_id=episode.series_id, number=episode.number,
        title=episode.title, premise=episode.premise, status=episode.status.value,
        target_seconds=episode.target_seconds, duration_seconds=episode.duration_seconds,
        shot_count=episode.shot_count, video_url=episode.video_url,
        thumbnail_url=storage.url_for(episode.thumbnail_key) if episode.thumbnail_key else None,
        subtitle_url=storage.url_for(episode.subtitle_key) if episode.subtitle_key else None,
        cost_usd=episode.cost_usd, created_at=episode.created_at,
    )


def _job_out(job: Job, *, include_result: bool = False) -> JobOut:
    return JobOut(
        id=job.id, org_id=job.org_id, episode_id=job.episode_id, status=job.status.value,
        stage=job.stage, progress_pct=job.progress_pct, message=job.message,
        cost_usd=job.cost_usd, credits_reserved=job.credits_reserved,
        credits_charged=job.credits_charged, error=job.error, created_at=job.created_at,
        started_at=job.started_at, finished_at=job.finished_at,
        result=job.result if include_result else None,
    )
