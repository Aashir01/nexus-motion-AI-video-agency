"""Generation stages: reference sheets, keyframes, dialogue, shots, score."""
from __future__ import annotations

import asyncio

from nexus.agents.visual import (
    build_location_plate_prompt,
    build_portrait_prompt,
    build_video_prompt,
)
from nexus.pipeline.assets import persist
from nexus.pipeline.context import ProductionContext
from nexus.routing.router import Requirements
from nexus.routing.types import AudioRequest, ImageRequest, TTSRequest, VideoRequest
from nexus.story.schemas import ReferenceImage, Shot, ShotStatus
from nexus.util.errors import NexusError
from nexus.util.logging import get_logger

log = get_logger(__name__)

_PORTRAIT_KINDS = ["portrait_front", "portrait_three_quarter", "full_body", "portrait_profile"]


async def stage_reference_sheets(ctx: ProductionContext) -> None:
    """Generate the canonical likeness anchors.

    The first portrait is text-to-image from the appearance block. Every
    subsequent angle is an *edit* conditioned on that first image, which is what
    makes the set look like one person rather than four siblings. These images
    are then reused for every shot in every episode of the series.
    """
    need = [c for c in ctx.bible.characters if not c.reference_images]
    plates_needed = (
        [loc for loc in ctx.bible.locations if not loc.reference_images]
        if ctx.options.generate_location_plates else []
    )
    if not need and not plates_needed:
        await ctx.emit("skipped", "reference sheets already exist")
        return

    total = len(need) + len(plates_needed)
    await ctx.emit("started", f"building {total} reference sheet(s)", total=total)
    done = 0
    per_character = max(1, min(ctx.options.reference_images_per_character, len(_PORTRAIT_KINDS)))

    for character in need:
        ctx.raise_if_cancelled()
        anchor_url: str | None = None
        for i, kind in enumerate(_PORTRAIT_KINDS[:per_character]):
            prompt = build_portrait_prompt(character, ctx.bible, kind)
            request = ImageRequest(
                prompt=prompt,
                negative_prompt=ctx.bible.visual_style.negative_prompt,
                aspect_ratio="1:1" if kind.startswith("portrait") else "4:5",
                resolution=ctx.options.resolution,
                label=f"{character.name} {kind.replace('_', ' ')}",
                seed=ctx.options.seed,
            )
            requirements = Requirements()
            if anchor_url:
                request.edit_source_url = anchor_url
                request.reference_image_urls = [anchor_url]
                requirements.needs_image_edit = True
                requirements.needs_reference_images = True

            try:
                response = await ctx.router.image("portrait", request, requirements=requirements)
                asset = await persist(
                    ctx, response, f"references/characters/{character.id}/{kind}"
                )
            except NexusError as exc:
                ctx.warn(f"reference {kind} for {character.name} failed: {exc}")
                continue

            reference = ReferenceImage(
                kind=kind, label=f"{character.name} — {kind}", asset_key=asset.key,
                url=asset.url, prompt_used=prompt, model_id=response.model_id,
                is_canonical=(i == 0),
            )
            character.reference_images.append(reference)
            if i == 0:
                anchor_url = asset.url

        if not character.reference_images:
            ctx.warn(
                f"no reference images for {character.name}; shots will rely on the text "
                f"identity block alone and consistency will be weaker"
            )
        done += 1
        await ctx.emit("progress", f"{character.name}: {len(character.reference_images)} refs",
                       completed=done, total=total)
        await ctx.checkpoint()

    for location in plates_needed:
        ctx.raise_if_cancelled()
        prompt = build_location_plate_prompt(location, ctx.bible)
        try:
            response = await ctx.router.image("location_plate", ImageRequest(
                prompt=prompt, negative_prompt=ctx.bible.visual_style.negative_prompt,
                aspect_ratio=ctx.options.aspect_ratio, resolution=ctx.options.resolution,
                label=f"{location.name} plate", seed=ctx.options.seed,
            ))
            asset = await persist(ctx, response, f"references/locations/{location.id}/plate")
            location.reference_images.append(ReferenceImage(
                kind="location_plate", label=location.name, asset_key=asset.key, url=asset.url,
                prompt_used=prompt, model_id=response.model_id, is_canonical=True,
            ))
        except NexusError as exc:
            ctx.warn(f"location plate for {location.name} failed: {exc}")
        done += 1
        await ctx.emit("progress", f"{location.name} plate", completed=done, total=total)

    await ctx.checkpoint()
    await ctx.emit(
        "completed",
        f"{sum(len(c.reference_images) for c in ctx.bible.characters)} character refs, "
        f"{sum(len(loc.reference_images) for loc in ctx.bible.locations)} plates",
    )


async def stage_keyframes(ctx: ProductionContext) -> None:
    """One conditioned still per shot. This is the identity checkpoint: if the
    face is right here, image-to-video will carry it through the clip."""
    shots = [s for s in ctx.episode.all_shots() if not s.keyframe_url and s.status != ShotStatus.SKIPPED]
    if not shots:
        await ctx.emit("skipped", "keyframes already rendered")
        return

    await ctx.emit("started", f"rendering {len(shots)} keyframes", total=len(shots))
    semaphore = asyncio.Semaphore(ctx.options.shot_concurrency)
    done = 0
    failures = 0

    async def one(shot: Shot) -> None:
        nonlocal done, failures
        async with semaphore:
            ctx.raise_if_cancelled()
            request = ImageRequest(
                prompt=shot.keyframe_prompt or shot.action,
                negative_prompt=shot.negative_prompt or ctx.bible.visual_style.negative_prompt,
                reference_image_urls=shot.reference_image_urls,
                edit_source_url=shot.reference_image_urls[0] if shot.reference_image_urls else None,
                aspect_ratio=ctx.options.aspect_ratio,
                resolution=ctx.options.resolution,
                label=f"shot {shot.index:03d} {shot.shot_size.value}",
                seed=ctx.options.seed,
            )
            requirements = Requirements(
                needs_reference_images=bool(shot.reference_image_urls),
                needs_image_edit=bool(shot.reference_image_urls),
            )
            try:
                response = await ctx.router.image("keyframe", request, requirements=requirements)
                asset = await persist(ctx, response, f"shots/{shot.index:04d}/keyframe")
            except NexusError as exc:
                failures += 1
                shot.failure_reason = f"keyframe: {exc}"
                ctx.warn(f"keyframe failed for shot {shot.index}: {exc}")
            else:
                shot.keyframe_asset_key = asset.key
                shot.keyframe_url = asset.url
                shot.status = ShotStatus.KEYFRAME_READY
                shot.models_used["keyframe"] = response.model_id
                shot.cost_usd += response.cost_usd
            done += 1
            if done % 5 == 0 or done == len(shots):
                await ctx.checkpoint()
            await ctx.emit("progress", f"keyframe {done}/{len(shots)}",
                           completed=done, total=len(shots))

    await asyncio.gather(*(one(s) for s in shots))
    await ctx.checkpoint()
    await ctx.emit("completed", f"{len(shots) - failures}/{len(shots)} keyframes rendered")


async def stage_dialogue(ctx: ProductionContext) -> None:
    """Synthesise every line, and use the real audio length to fix shot timing."""
    lines = [
        (shot, i, line)
        for shot in ctx.episode.all_shots()
        for i, line in enumerate(shot.dialogue)
        if not line.audio_url
    ]
    if not lines:
        await ctx.emit("skipped", "no dialogue to synthesise")
        return

    await ctx.emit("started", f"recording {len(lines)} lines", total=len(lines))
    semaphore = asyncio.Semaphore(max(4, ctx.options.shot_concurrency))
    done = 0

    async def one(shot: Shot, index: int, line) -> None:
        nonlocal done
        async with semaphore:
            ctx.raise_if_cancelled()
            character = ctx.bible.character(line.character_id)
            voice = character.voice if character else None
            request = TTSRequest(
                text=line.text,
                voice_id=voice.provider_voice_id if voice else None,
                language=ctx.bible.language,
                speed=voice.default_pace if voice else 1.0,
                stability=voice.default_stability if voice else 0.5,
                style=character.speech_style if character else "",
                emotion=line.emotion,
                label=f"shot {shot.index:03d} line {index}",
            )
            try:
                response = await ctx.router.speech("dialogue", request)
                asset = await persist(ctx, response, f"shots/{shot.index:04d}/line_{index:02d}")
            except NexusError as exc:
                ctx.warn(f"dialogue failed for shot {shot.index} line {index}: {exc}")
            else:
                line.audio_asset_key = asset.key
                line.audio_url = asset.url
                line.actual_seconds = response.duration_s or await _probe_duration(ctx, asset.key)
                shot.models_used["dialogue"] = response.model_id
                shot.cost_usd += response.cost_usd
            done += 1
            await ctx.emit("progress", f"line {done}/{len(lines)}",
                           completed=done, total=len(lines))

    await asyncio.gather(*(one(shot, idx, line) for shot, idx, line in lines))

    # Real audio durations beat the model's guess — retime every shot to fit.
    for shot in ctx.episode.all_shots():
        if shot.dialogue:
            needed = shot.dialogue_seconds() + 0.9
            shot.target_seconds = max(shot.target_seconds, min(needed, 12.0))

    await ctx.checkpoint()
    await ctx.emit("completed",
                   f"{len(lines)} lines recorded; runtime now {ctx.episode.planned_seconds():.0f}s")


async def _probe_duration(ctx: ProductionContext, key: str) -> float:
    from nexus.render.ffmpeg import probe_duration

    try:
        path = await ctx.storage.local_path(key)
        return await probe_duration(path)
    except Exception:
        return 0.0


async def stage_shots(ctx: ProductionContext) -> None:
    """Generate the clips. The expensive stage — parallel, resumable, per-shot."""
    shots = [
        s for s in ctx.episode.all_shots()
        if not s.video_url and s.status != ShotStatus.SKIPPED
    ]
    if not shots:
        await ctx.emit("skipped", "all shots already generated")
        return

    await ctx.emit("started", f"generating {len(shots)} shots", total=len(shots))
    semaphore = asyncio.Semaphore(ctx.options.shot_concurrency)
    done = 0
    failures = 0

    async def one(shot: Shot) -> None:
        nonlocal done, failures
        async with semaphore:
            ctx.raise_if_cancelled()
            duration = min(max(shot.planned_seconds(), 3.0), 10.0)
            has_keyframe = bool(shot.keyframe_url)
            prompt = shot.video_prompt or shot.action
            if not has_keyframe:
                scene = next((s for s in ctx.episode.scenes if s.id == shot.scene_id), None)
                location = ctx.bible.location(scene.location_id) if scene else None
                prompt = build_video_prompt(
                    shot, shot.action, ctx.bible, location, shot.continuity_in, has_keyframe=False
                )

            request = VideoRequest(
                prompt=prompt,
                negative_prompt=shot.negative_prompt or ctx.bible.visual_style.negative_prompt,
                first_frame_url=shot.keyframe_url,
                reference_image_urls=shot.reference_image_urls,
                duration_s=duration,
                aspect_ratio=ctx.options.aspect_ratio,
                resolution=ctx.options.resolution,
                fps=ctx.bible.visual_style.frame_rate,
                with_audio=False,          # dialogue is synthesised separately and timed to the cut
                camera_hint=shot.camera_move.value,
                seed=ctx.options.seed,
                label=f"shot {shot.index:03d}",
            )
            try:
                response = await ctx.router.video("shot_video", request)
                asset = await persist(ctx, response, f"shots/{shot.index:04d}/clip")
            except NexusError as exc:
                failures += 1
                shot.status = ShotStatus.FAILED
                shot.failure_reason = f"video: {exc}"
                shot.attempts += 1
                ctx.warn(f"shot {shot.index} failed: {exc}")
            else:
                shot.video_asset_key = asset.key
                shot.video_url = asset.url
                shot.actual_seconds = response.duration_s or duration
                shot.status = ShotStatus.VIDEO_READY
                shot.attempts += 1
                shot.models_used["video"] = response.model_id
                shot.cost_usd += response.cost_usd
            done += 1
            if done % 3 == 0 or done == len(shots):
                await ctx.checkpoint()
            await ctx.emit(
                "progress", f"shot {done}/{len(shots)}", completed=done, total=len(shots),
                spent_usd=round(ctx.router.ledger.spent_usd, 3),
            )

    await asyncio.gather(*(one(s) for s in shots))
    await ctx.checkpoint()

    if failures:
        ctx.warn(f"{failures} shot(s) could not be generated and will be cut from the assembly")
    await ctx.emit("completed", f"{len(shots) - failures}/{len(shots)} shots generated")


async def stage_score(ctx: ProductionContext) -> None:
    """One continuous music bed for the whole episode, ducked under dialogue at mix."""
    if not ctx.options.generate_score:
        await ctx.emit("skipped", "score disabled")
        return

    key = "audio/score"
    if await ctx.storage.exists(ctx.key(f"{key}.wav")) or await ctx.storage.exists(ctx.key(f"{key}.mp3")):
        await ctx.emit("skipped", "score already generated")
        return

    duration = max(30.0, ctx.episode.planned_seconds())
    await ctx.emit("started", f"scoring {duration:.0f}s")
    brief = (
        f"{ctx.bible.audio_style.score_direction}. Genre: {ctx.bible.genre}. "
        f"Tone: {ctx.bible.tone}. Intensity curve: {ctx.bible.audio_style.music_intensity_curve}. "
        f"Instrumental only, no vocals, no percussion-led sections, sits under dialogue."
    )
    try:
        response = await ctx.router.audio(
            "score", AudioRequest(prompt=brief, duration_s=duration, kind="music", label="score")
        )
        asset = await persist(ctx, response, key)
    except NexusError as exc:
        ctx.warn(f"score generation failed ({exc}); the episode will be mixed without music")
        await ctx.emit("completed", "no score")
        return

    ctx.episode.score_asset_key = asset.key
    await ctx.checkpoint()
    await ctx.emit("completed", f"score ready ({duration:.0f}s)", model=response.model_id)
