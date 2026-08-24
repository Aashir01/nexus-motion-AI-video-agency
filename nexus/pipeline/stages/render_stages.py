"""Assembly and review stages."""
from __future__ import annotations

import tempfile
from pathlib import Path

from nexus.agents.quality import CriticAgent
from nexus.pipeline.context import ProductionContext
from nexus.render.assembly import RenderRequest, render_episode
from nexus.story.schemas import ShotStatus
from nexus.util.errors import NexusError, RenderError


async def stage_assemble(ctx: ProductionContext) -> None:
    """Pull every asset local, render the cut, push the results back."""
    if ctx.episode.final_video_url:
        await ctx.emit("skipped", "episode already assembled")
        return

    shots = [s for s in ctx.episode.all_shots() if s.video_asset_key]
    if not shots:
        raise RenderError("cannot assemble: no shots were generated successfully")

    await ctx.emit("started", f"assembling {len(shots)} shots")

    with tempfile.TemporaryDirectory(prefix=f"nexus-{ctx.job_id}-") as tmp:
        workdir = Path(tmp)
        video_paths: dict[int, Path] = {}
        dialogue_paths: dict[tuple[int, int], Path] = {}

        for shot in shots:
            try:
                video_paths[shot.index] = await ctx.storage.local_path(shot.video_asset_key)
            except Exception as exc:
                ctx.warn(f"shot {shot.index} clip unreadable ({exc}); dropped from the cut")
                continue
            for i, line in enumerate(shot.dialogue):
                if line.audio_asset_key:
                    try:
                        dialogue_paths[(shot.index, i)] = await ctx.storage.local_path(
                            line.audio_asset_key
                        )
                    except Exception as exc:
                        ctx.warn(f"dialogue audio missing for shot {shot.index} line {i}: {exc}")

        score_path = None
        if ctx.episode.score_asset_key:
            try:
                score_path = await ctx.storage.local_path(ctx.episode.score_asset_key)
            except Exception as exc:
                ctx.warn(f"score unreadable ({exc}); mixing without music")

        await ctx.emit("progress", "rendering", completed=1, total=3)
        result = await render_episode(RenderRequest(
            episode=ctx.episode, bible=ctx.bible, workdir=workdir,
            shot_video_paths=video_paths, dialogue_paths=dialogue_paths,
            score_path=score_path, resolution=ctx.options.resolution,
            aspect_ratio=ctx.options.aspect_ratio, fps=ctx.bible.visual_style.frame_rate,
            burn_subtitles=ctx.options.burn_subtitles,
            write_subtitles=ctx.options.subtitles,
        ))

        await ctx.emit("progress", "publishing", completed=2, total=3)
        video = await ctx.storage.put_file(
            ctx.key(f"episode_{ctx.episode.number:02d}.mp4"), result.video_path, "video/mp4"
        )
        ctx.episode.final_video_asset_key = video.key
        ctx.episode.final_video_url = video.url
        ctx.episode.actual_seconds = result.duration_s

        if result.thumbnail_path:
            thumb = await ctx.storage.put_file(
                ctx.key("thumbnail.jpg"), result.thumbnail_path, "image/jpeg"
            )
            ctx.episode.thumbnail_asset_key = thumb.key
        if result.subtitle_path:
            subs = await ctx.storage.put_file(
                ctx.key("subtitles.srt"), result.subtitle_path, "application/x-subrip"
            )
            ctx.episode.subtitle_asset_key = subs.key

    for shot in shots:
        if shot.status == ShotStatus.VIDEO_READY:
            shot.status = ShotStatus.COMPLETE

    await ctx.checkpoint()
    await ctx.emit(
        "completed",
        f"{result.duration_s / 60:.1f} min episode from {result.shots_used} shots"
        + (f" ({result.shots_skipped} dropped)" if result.shots_skipped else ""),
        duration_s=round(result.duration_s, 1), url=ctx.episode.final_video_url,
    )


async def stage_critique(ctx: ProductionContext) -> None:
    """Script-editor pass over the finished cut."""
    if not ctx.options.critique:
        await ctx.emit("skipped", "critique disabled")
        return

    await ctx.emit("started", "reviewing the cut")
    try:
        report = await CriticAgent(ctx.router).review(ctx.bible, ctx.episode)
    except NexusError as exc:
        ctx.warn(f"critique unavailable: {exc}")
        await ctx.emit("completed", "critique skipped")
        return

    flags = {f.shot_index: f for f in report.shot_flags}
    for shot in ctx.episode.all_shots():
        flag = flags.get(shot.index)
        if flag:
            shot.critic_score = (flag.identity_consistency + flag.staging_quality) / 2
            shot.critic_notes = flag.notes

    ctx.plan.warnings.extend(f"critic: {p}" for p in report.problems[:6])
    await ctx.emit(
        "completed",
        f"verdict: {report.verdict} "
        f"(coherence {report.narrative_coherence}/10, consistency {report.character_consistency}/10)",
        verdict=report.verdict,
        scores={
            "narrative_coherence": report.narrative_coherence,
            "character_consistency": report.character_consistency,
            "pacing": report.pacing,
            "dialogue_quality": report.dialogue_quality,
        },
        problems=report.problems[:8],
        flagged_shots=[f.shot_index for f in report.shot_flags if f.should_regenerate],
    )
