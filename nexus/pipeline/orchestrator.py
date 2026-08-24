"""The production orchestrator.

Stages run in a fixed order, each one idempotent: it inspects the plan, does the
work that is missing, and checkpoints.  That property is what makes a 40-minute,
130-shot job survivable — a worker that dies at shot 96 restarts and picks up at
shot 96, not at the show bible.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nexus.pipeline.context import ProductionContext
from nexus.pipeline.stages import asset_stages, render_stages, story_stages
from nexus.util.errors import NexusError, PipelineError
from nexus.util.logging import get_logger

log = get_logger(__name__)

StageFn = Callable[[ProductionContext], Awaitable[None]]


@dataclass(slots=True)
class Stage:
    name: str
    label: str
    run: StageFn
    weight: float = 1.0
    critical: bool = True     # a non-critical failure is logged and the run continues


def build_stages(brief: str, premise: str, recap: str = "") -> list[Stage]:
    """Weights approximate real wall-clock share so the progress bar doesn't lie."""
    return [
        Stage("bible", "Show bible",
              lambda ctx: story_stages.stage_bible(ctx, brief), weight=4),
        Stage("outline", "Episode outline",
              lambda ctx: story_stages.stage_outline(ctx, premise, recap), weight=3),
        Stage("scene_grid", "Scene grid", story_stages.stage_scene_grid, weight=3),
        Stage("storyboard", "Shot breakdown", story_stages.stage_storyboard, weight=8),
        Stage("continuity", "Continuity audit", story_stages.stage_continuity,
              weight=4, critical=False),
        Stage("casting", "Voice casting", story_stages.stage_casting, weight=1, critical=False),
        Stage("references", "Character reference sheets",
              asset_stages.stage_reference_sheets, weight=8),
        Stage("prompts", "Shot prompts", story_stages.stage_prompts, weight=5),
        Stage("keyframes", "Keyframes", asset_stages.stage_keyframes, weight=15),
        Stage("dialogue", "Dialogue recording", asset_stages.stage_dialogue, weight=8),
        Stage("shots", "Shot generation", asset_stages.stage_shots, weight=32),
        Stage("score", "Score", asset_stages.stage_score, weight=3, critical=False),
        Stage("assemble", "Assembly", render_stages.stage_assemble, weight=5),
        Stage("critique", "Review", render_stages.stage_critique, weight=1, critical=False),
    ]


class Orchestrator:
    def __init__(self, ctx: ProductionContext, stages: list[Stage]):
        self.ctx = ctx
        self.stages = stages
        ctx.register_stages({s.name: s.weight for s in stages})

    async def run(self, *, start_from: str | None = None) -> dict:
        ctx = self.ctx
        started = time.time()
        skipping = start_from is not None

        await ctx.emit("started", "production started", stage="pipeline")

        for stage in self.stages:
            if skipping:
                if stage.name != start_from:
                    ctx.finish_stage(stage.name)
                    continue
                skipping = False

            ctx.begin_stage(stage.name)
            stage_started = time.time()
            try:
                await stage.run(ctx)
                status = "completed"
                error = None
            except asyncio.CancelledError:
                await ctx.emit("failed", "cancelled", stage=stage.name)
                raise
            except NexusError as exc:
                status, error = "failed", str(exc)
                log.error("stage_failed", extra={"stage": stage.name, "error": error[:500],
                                                 "code": exc.code})
                if stage.critical:
                    await ctx.emit("failed", error, stage=stage.name, code=exc.code)
                    ctx.stage_history.append(_record(stage, stage_started, status, error))
                    raise PipelineError(
                        f"production failed during '{stage.label}': {exc}",
                        detail={"stage": stage.name, "cause": exc.code,
                                "spent_usd": round(ctx.router.ledger.spent_usd, 4)},
                    ) from exc
                ctx.warn(f"{stage.label} failed but is non-critical: {exc}")
                await ctx.emit("failed", error, stage=stage.name, recoverable=True)
            except Exception as exc:
                log.exception("stage_crashed", extra={"stage": stage.name})
                if stage.critical:
                    ctx.stage_history.append(_record(stage, stage_started, "failed", repr(exc)))
                    raise PipelineError(
                        f"production crashed during '{stage.label}': {exc}",
                        detail={"stage": stage.name},
                    ) from exc
                status, error = "failed", repr(exc)
                ctx.warn(f"{stage.label} crashed but is non-critical: {exc}")
            else:
                pass

            ctx.finish_stage(stage.name)
            ctx.stage_history.append(_record(stage, stage_started, status, error))

        await ctx.checkpoint()
        summary = ctx.summary()
        summary["elapsed_s"] = round(time.time() - started, 1)
        await ctx.emit("completed", "production complete", stage="pipeline", duration_s=ctx.episode.actual_seconds, spent_usd=round(ctx.router.ledger.spent_usd, 4))
        return summary


def _record(stage: Stage, started: float, status: str, error: str | None) -> dict:
    return {
        "stage": stage.name, "label": stage.label, "status": status,
        "elapsed_s": round(time.time() - started, 2),
        **({"error": error[:400]} if error else {}),
    }
