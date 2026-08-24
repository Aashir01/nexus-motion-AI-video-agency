"""Entry point that wires options → router → context → orchestrator."""
from __future__ import annotations

from dataclasses import dataclass

from nexus.pipeline.context import (
    ProductionContext,
    ProductionOptions,
    ProgressHook,
    new_job_id,
)
from nexus.pipeline.orchestrator import Orchestrator, build_stages
from nexus.routing.catalog import CATALOG
from nexus.routing.ledger import CostLedger
from nexus.routing.router import ModelRouter
from nexus.storage import get_storage
from nexus.story.schemas import Episode, ProductionPlan, SeriesBible
from nexus.util.ids import new_id


@dataclass(slots=True)
class ProductionRequest:
    brief: str
    premise: str = ""
    episode_number: int = 1
    recap: str = ""
    options: ProductionOptions | None = None
    budget_usd: float | None = None
    model_overrides: dict[str, str] | None = None
    allowed_models: set[str] | None = None
    bible: SeriesBible | None = None
    job_id: str | None = None


def offline_model_ids() -> set[str]:
    return {mid for mid, spec in CATALOG.items() if spec.provider == "simulation"}


def build_context(
    request: ProductionRequest, *, progress_hook: ProgressHook | None = None
) -> ProductionContext:
    options = request.options or ProductionOptions()
    allowed = request.allowed_models
    if options.profile == "offline":
        allowed = offline_model_ids()

    router = ModelRouter(
        profile=options.profile,
        ledger=CostLedger(budget_usd=request.budget_usd),
        allowed_models=allowed,
        overrides=request.model_overrides,
        allow_simulation=True,
    )

    bible = request.bible or SeriesBible(id=new_id("srs"), title="", logline="")
    episode = Episode(
        id=new_id("ep"), series_id=bible.id, number=request.episode_number,
        target_seconds=options.target_seconds,
    )
    plan = ProductionPlan(bible=bible, episode=episode)

    return ProductionContext(
        job_id=request.job_id or new_job_id(),
        plan=plan, router=router, options=options,
        storage=get_storage(), progress_hook=progress_hook,
    )


async def produce(
    request: ProductionRequest,
    *,
    progress_hook: ProgressHook | None = None,
    resume_from: str | None = None,
    context: ProductionContext | None = None,
) -> dict:
    ctx = context or build_context(request, progress_hook=progress_hook)
    if context is not None and progress_hook is not None:
        ctx.progress_hook = progress_hook
    stages = build_stages(request.brief, request.premise, request.recap)
    return await Orchestrator(ctx, stages).run(start_from=resume_from)


async def resume(
    job_id: str, request: ProductionRequest, *, progress_hook: ProgressHook | None = None
) -> dict:
    """Reload a checkpointed plan and continue. Stages are idempotent, so simply
    re-running the whole list picks up exactly where it stopped."""
    ctx = build_context(
        ProductionRequest(**{**request.__dict__, "job_id": job_id}), progress_hook=progress_hook
    )
    saved = await ctx.load_checkpoint()
    if saved:
        ctx.plan = saved
    return await produce(request, context=ctx)
