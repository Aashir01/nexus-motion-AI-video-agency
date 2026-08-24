"""Writing stages: bible → outline → scene grid → storyboard → continuity → prompts."""
from __future__ import annotations

import asyncio

from nexus.agents.io import BibleDraft
from nexus.agents.quality import (
    CastingAgent,
    ContinuityAgent,
    apply_audit,
    seed_continuity,
)
from nexus.agents.story import (
    SceneBreakdownAgent,
    ScreenwriterAgent,
    ShowrunnerAgent,
    StoryboardAgent,
)
from nexus.agents.visual import (
    PromptSmithAgent,
    build_keyframe_prompt,
    build_video_prompt,
)
from nexus.pipeline.context import ProductionContext
from nexus.providers.edge_provider import VOICE_POOL, pick_voice
from nexus.story.schemas import (
    Act,
    CharacterProfile,
    LocationProfile,
    Scene,
    Shot,
    StoryBeat,
    VoiceProfile,
)
from nexus.util.ids import new_id
from nexus.util.logging import get_logger

log = get_logger(__name__)

MAX_SHOT_SECONDS = 9.0
MIN_SHOT_SECONDS = 3.0


async def stage_bible(ctx: ProductionContext, brief: str) -> None:
    """Create the show bible, unless the project already has one."""
    if ctx.bible.characters:
        await ctx.emit("skipped", "reusing existing series bible")
        return

    await ctx.emit("started", "writing the show bible")
    draft: BibleDraft = await ShowrunnerAgent(ctx.router).create(
        brief,
        target_minutes=ctx.options.target_seconds / 60,
        language=ctx.options.language,
        content_rating=ctx.options.content_rating,
    )
    _apply_bible(ctx, draft)
    await ctx.checkpoint()
    await ctx.emit(
        "completed",
        f"{draft.title}: {len(draft.characters)} characters, {len(draft.locations)} locations",
        characters=[c.name for c in draft.characters],
    )


def _apply_bible(ctx: ProductionContext, draft: BibleDraft) -> None:
    bible = ctx.bible
    bible.title = draft.title
    bible.logline = draft.logline
    bible.genre = draft.genre
    bible.tone = draft.tone
    bible.themes = draft.themes
    bible.setting = draft.setting
    bible.series_arc = draft.series_arc
    bible.visual_style = draft.visual_style
    bible.visual_style.aspect_ratio = ctx.options.aspect_ratio  # type: ignore[assignment]
    bible.visual_style.resolution = ctx.options.resolution      # type: ignore[assignment]
    bible.audio_style = draft.audio_style
    bible.language = ctx.options.language
    bible.content_rating = ctx.options.content_rating           # type: ignore[assignment]

    bible.characters = [
        CharacterProfile(
            id=_slug(c.id or c.name),
            name=c.name,
            role=c.role if c.role in
            ("protagonist", "antagonist", "deuteragonist", "supporting", "minor") else "supporting",
            one_line=c.one_line, backstory=c.backstory, motivation=c.motivation,
            flaw=c.flaw, arc=c.arc, speech_style=c.speech_style, appearance=c.appearance,
            voice=VoiceProfile(
                gender=c.voice_gender if c.voice_gender in ("male", "female", "neutral") else "neutral",
                age_bracket=c.voice_age_bracket, accent=c.voice_accent, timbre=c.voice_timbre,
            ),
            consistency_token=_slug(c.id or c.name).upper(),
        )
        for c in draft.characters
    ]
    bible.locations = [
        LocationProfile(
            id=_slug(loc.id or loc.name), name=loc.name, description=loc.description,
            interior_exterior=loc.interior_exterior if loc.interior_exterior in ("INT", "EXT", "INT/EXT") else "INT",
            architecture=loc.architecture, palette=loc.palette,
            lighting_signature=loc.lighting_signature, props=loc.props,
            ambience_sound=loc.ambience_sound,
        )
        for loc in draft.locations
    ]


def _slug(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "unnamed"


async def stage_casting(ctx: ProductionContext) -> None:
    """Assign a distinct voice to every character."""
    if all(c.voice.provider_voice_id for c in ctx.bible.characters):
        await ctx.emit("skipped", "voices already cast")
        return

    await ctx.emit("started", "casting voices")
    available = [v for pool in VOICE_POOL.values() for v in pool]
    try:
        sheet = await CastingAgent(ctx.router).cast(ctx.bible, available)
        chosen = {a.character_id: a for a in sheet.assignments}
    except Exception as exc:
        ctx.warn(f"casting agent failed ({exc}); falling back to rule-based voice assignment")
        chosen = {}

    taken: set[str] = set()
    for character in ctx.bible.characters:
        assignment = chosen.get(character.id)
        voice = assignment.voice_name if assignment and assignment.voice_name in available else None
        if not voice or voice in taken:
            voice = pick_voice(character.voice.gender, character.voice.age_bracket, taken)
        taken.add(voice)
        character.voice.provider_voice_id = voice
        character.voice.voice_name = voice
        if assignment:
            character.voice.default_pace = assignment.default_pace
            character.voice.default_stability = assignment.default_stability

    await ctx.checkpoint()
    await ctx.emit("completed", f"cast {len(taken)} voices",
                   voices={c.name: c.voice.voice_name for c in ctx.bible.characters})


async def stage_outline(ctx: ProductionContext, premise: str, recap: str = "") -> None:
    """Act structure and beat sheet."""
    if ctx.episode.beats:
        await ctx.emit("skipped", "outline already written")
        return

    await ctx.emit("started", "breaking the episode")
    outline = await ScreenwriterAgent(ctx.router).outline(
        ctx.bible, episode_number=ctx.episode.number, premise=premise,
        target_seconds=ctx.options.target_seconds, previous_recap=recap,
    )
    ep = ctx.episode
    ep.title = outline.title
    ep.logline = outline.logline
    ep.premise = outline.premise
    ep.cold_open = outline.cold_open
    ep.cliffhanger = outline.cliffhanger
    ep.acts = [Act(**a.model_dump()) for a in outline.acts]
    ep.beats = [StoryBeat(**b.model_dump()) for b in outline.beats]

    await ctx.checkpoint()
    await ctx.emit("completed", f"{outline.title} — {len(ep.beats)} beats across {len(ep.acts)} acts")


async def stage_scene_grid(ctx: ProductionContext) -> None:
    """Beat sheet → scene grid, normalised to hit the target runtime."""
    if ctx.episode.scenes:
        await ctx.emit("skipped", "scene grid already built")
        return

    await ctx.emit("started", "laying out scenes")
    from nexus.agents.io import EpisodeOutline

    outline = EpisodeOutline(
        title=ctx.episode.title, logline=ctx.episode.logline, premise=ctx.episode.premise,
        cold_open=ctx.episode.cold_open, cliffhanger=ctx.episode.cliffhanger,
        acts=ctx.episode.acts, beats=ctx.episode.beats,
    )
    grid = await SceneBreakdownAgent(ctx.router).breakdown(
        ctx.bible, outline, ctx.options.target_seconds
    )

    known_locations = {loc.id for loc in ctx.bible.locations}
    known_characters = {c.id for c in ctx.bible.characters}
    scenes: list[Scene] = []
    for i, draft in enumerate(sorted(grid.scenes, key=lambda s: s.index), start=1):
        location_id = draft.location_id if draft.location_id in known_locations else (
            ctx.bible.locations[0].id if ctx.bible.locations else "unknown"
        )
        if draft.location_id not in known_locations:
            ctx.warn(f"scene {i} referenced unknown location {draft.location_id!r}; "
                     f"remapped to {location_id!r}")
        present = [c for c in draft.characters_present if c in known_characters]
        if not present:
            # A scene with nobody in it cannot be shot. Fall back to the leads
            # rather than silently producing an empty plate.
            present = [c.id for c in ctx.bible.characters[:2]]
            ctx.warn(f"scene {i} listed no known characters; defaulting to the leads")
        scenes.append(Scene(
            id=new_id("scn"), episode_id=ctx.episode.id, index=i,
            act_number=draft.act_number, slug_line=draft.slug_line, location_id=location_id,
            time_of_day=draft.time_of_day, synopsis=draft.synopsis,
            dramatic_function=draft.dramatic_function, characters_present=present,
            target_seconds=draft.target_seconds,
        ))

    _normalise_scene_durations(scenes, ctx.options.target_seconds)
    ctx.episode.scenes = scenes
    await ctx.checkpoint()
    await ctx.emit(
        "completed",
        f"{len(scenes)} scenes totalling {sum(s.target_seconds for s in scenes):.0f}s",
        scenes=[{"index": s.index, "slug": s.slug_line, "seconds": s.target_seconds} for s in scenes],
    )


def _normalise_scene_durations(scenes: list[Scene], target_seconds: float) -> None:
    """Models are unreliable at arithmetic. Scale the grid so the episode really
    lands on its target runtime instead of 40% short."""
    total = sum(s.target_seconds for s in scenes)
    if total <= 0:
        for s in scenes:
            s.target_seconds = target_seconds / max(1, len(scenes))
        return
    factor = target_seconds / total
    if 0.9 <= factor <= 1.1:
        return
    for s in scenes:
        s.target_seconds = max(15.0, min(120.0, s.target_seconds * factor))


async def stage_storyboard(ctx: ProductionContext) -> None:
    """Scene by scene: shot list + dialogue, sequentially so continuity carries."""
    pending = [s for s in ctx.episode.scenes if not s.shots]
    if not pending:
        await ctx.emit("skipped", "all scenes already boarded")
        return

    await ctx.emit("started", f"boarding {len(pending)} scenes", total=len(pending))
    agent = StoryboardAgent(ctx.router)
    global_index = max((sh.index for sc in ctx.episode.scenes for sh in sc.shots), default=0)

    for done, scene in enumerate(pending, start=1):
        ctx.raise_if_cancelled()
        previous = next(
            (s for s in reversed(ctx.episode.scenes) if s.index < scene.index and s.shots), None
        )
        tail = previous.shots[-1].action if previous else ""
        continuity = previous.continuity_out.to_prompt_fragment() if previous and previous.continuity_out else ""

        try:
            script = await agent.board(ctx.bible, scene, previous_scene_tail=tail, continuity=continuity)
        except Exception as exc:
            ctx.warn(f"storyboard failed for scene {scene.index} ({exc}); using a single wide shot")
            script = None

        shots: list[Shot] = []
        drafts = script.shots if script else []
        if not drafts:
            shots.append(Shot(
                id=new_id("sht"), scene_id=scene.id, index=global_index + 1, scene_shot_index=0,
                action=scene.synopsis[:300], target_seconds=min(scene.target_seconds, MAX_SHOT_SECONDS),
                subject_ids=scene.characters_present[:2],
            ))
            global_index += 1
        else:
            known = {c.id for c in ctx.bible.characters}
            for j, draft in enumerate(drafts):
                global_index += 1
                subjects = [s for s in draft.subject_ids if s in known] or scene.characters_present[:1]
                shots.append(Shot(
                    id=new_id("sht"), scene_id=scene.id, index=global_index, scene_shot_index=j,
                    shot_size=draft.shot_size, camera_move=draft.camera_move,
                    transition_in=draft.transition_in, subject_ids=subjects,
                    action=draft.action, emotional_beat=draft.emotional_beat,
                    target_seconds=max(MIN_SHOT_SECONDS, min(draft.target_seconds, MAX_SHOT_SECONDS)),
                    sfx_prompt=draft.sfx_prompt,
                    dialogue=[
                        _dialogue_line(d, known, subjects) for d in draft.dialogue
                    ],
                ))
        scene.shots = shots
        if script and script.continuity_out_notes:
            scene.continuity_out = scene.continuity_out or None
        await ctx.checkpoint()
        await ctx.emit("progress", f"scene {scene.index} → {len(shots)} shots",
                       completed=done, total=len(pending))

    await ctx.emit("completed",
                   f"{ctx.episode.shot_count()} shots, {ctx.episode.planned_seconds():.0f}s planned")


def _dialogue_line(draft, known: set[str], subjects: list[str]):
    from nexus.story.schemas import DialogueLine

    cid = draft.character_id if draft.character_id in known else (
        subjects[0] if subjects else "NARRATOR"
    )
    line = DialogueLine(
        character_id=cid, text=draft.text, delivery=draft.delivery, emotion=draft.emotion
    )
    line.estimated_seconds = line.duration()
    return line


async def stage_continuity(ctx: ProductionContext) -> None:
    """Walk the episode scene by scene, carrying and correcting world state."""
    if not ctx.options.continuity_audit:
        await ctx.emit("skipped", "continuity audit disabled")
        return
    if all(s.continuity_out for s in ctx.episode.scenes):
        await ctx.emit("skipped", "continuity already resolved")
        return

    scenes = ctx.episode.scenes
    await ctx.emit("started", f"auditing continuity across {len(scenes)} scenes", total=len(scenes))
    agent = ContinuityAgent(ctx.router)
    state = seed_continuity(ctx.bible, scenes[0] if scenes else None)
    violations = 0

    for done, scene in enumerate(scenes, start=1):
        ctx.raise_if_cancelled()
        scene.continuity_in = state.fork()
        try:
            audit = await agent.audit(ctx.bible, scene, state)
            violations += len(audit.violations)
            for v in audit.violations[:3]:
                ctx.warn(f"scene {scene.index} continuity: {v}")
            state = apply_audit(state, audit, scene)
        except Exception as exc:
            ctx.warn(f"continuity audit failed for scene {scene.index} ({exc}); carrying state forward")
            state = state.fork()
            state.location_id = scene.location_id
            state.time_of_day = scene.time_of_day
        scene.continuity_out = state.fork()
        for shot in scene.shots:
            shot.continuity_in = scene.continuity_in
        ctx.plan.continuity_log.append(state.fork())
        await ctx.emit("progress", f"scene {scene.index} audited", completed=done, total=len(scenes))

    await ctx.checkpoint()
    await ctx.emit("completed", f"continuity resolved, {violations} issue(s) corrected")


async def stage_prompts(ctx: ProductionContext) -> None:
    """Write staging/motion per shot, then assemble the final prompts in code."""
    scenes = [s for s in ctx.episode.scenes if s.shots and not all(sh.video_prompt for sh in s.shots)]
    if not scenes:
        await ctx.emit("skipped", "prompts already written")
        return

    await ctx.emit("started", f"writing prompts for {len(scenes)} scenes", total=len(scenes))
    agent = PromptSmithAgent(ctx.router)
    semaphore = asyncio.Semaphore(max(2, ctx.options.shot_concurrency // 2))
    done = 0

    async def one(scene: Scene) -> None:
        nonlocal done
        async with semaphore:
            ctx.raise_if_cancelled()
            try:
                sheet = await agent.write(ctx.bible, scene)
                by_index = {p.shot_index: p for p in sheet.prompts}
            except Exception as exc:
                ctx.warn(f"prompt smith failed for scene {scene.index} ({exc}); using shot actions")
                by_index = {}

            location = ctx.bible.location(scene.location_id)
            for i, shot in enumerate(scene.shots):
                draft = by_index.get(i)
                staging = draft.staging if draft else shot.action
                motion = draft.motion if draft else shot.action
                if draft and draft.sfx_prompt and not shot.sfx_prompt:
                    shot.sfx_prompt = draft.sfx_prompt
                shot.keyframe_prompt = build_keyframe_prompt(
                    shot, staging, ctx.bible, location, shot.continuity_in
                )
                shot.video_prompt = build_video_prompt(
                    shot, motion, ctx.bible, location, shot.continuity_in, has_keyframe=True
                )
                shot.negative_prompt = ctx.bible.visual_style.negative_prompt
                shot.reference_image_urls = _reference_urls_for(ctx, shot)
            done += 1
            await ctx.emit("progress", f"scene {scene.index} prompts ready",
                           completed=done, total=len(scenes))

    await asyncio.gather(*(one(s) for s in scenes))
    await ctx.checkpoint()
    await ctx.emit("completed", f"prompts written for {ctx.episode.shot_count()} shots")


def _reference_urls_for(ctx: ProductionContext, shot: Shot) -> list[str]:
    """Identity anchors for this shot: each character's canonical portrait, then
    the location plate if there is room left in the model's reference budget."""
    urls: list[str] = []
    for cid in shot.subject_ids[:3]:
        character = ctx.bible.character(cid)
        if character:
            urls.extend(character.reference_urls(limit=2 if len(shot.subject_ids) == 1 else 1))
    scene = next((s for s in ctx.episode.scenes if s.id == shot.scene_id), None)
    if scene:
        location = ctx.bible.location(scene.location_id)
        if location and location.reference_images:
            urls.append(location.reference_images[0].url or location.reference_images[0].asset_key)
    return [u for u in urls if u][:4]
