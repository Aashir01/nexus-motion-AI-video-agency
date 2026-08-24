"""End-to-end: brief in, playable episode out, using only the offline engine.

This is the test that proves the whole system holds together — writing,
continuity, references, keyframes, dialogue, shots, score, mux and subtitles —
with no API keys and no network.
"""
from __future__ import annotations

import pytest

from nexus.pipeline.context import ProductionOptions
from nexus.pipeline.runner import ProductionRequest, build_context, produce
from nexus.render.ffmpeg import ffmpeg_available, probe

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")

BRIEF = ("A harbour-town drama about Maya Rios, a fixer who has run out of favours, "
         "and Idris Vance, the man who is owed one.")


@pytest.mark.slow
async def test_offline_production_yields_a_playable_episode():
    events: list[tuple[str, str]] = []

    async def hook(event):
        events.append((event.stage, event.status))

    options = ProductionOptions(
        target_seconds=60, profile="offline", resolution="480p",
        reference_images_per_character=1, generate_score=True, critique=False,
    )
    request = ProductionRequest(brief=BRIEF, premise="One night to settle a debt",
                                options=options)
    context = build_context(request)
    summary = await produce(request, context=context, progress_hook=hook)

    episode = context.episode
    assert episode.scenes, "no scenes were written"
    assert episode.shot_count() >= 4

    # Every shot carries an assembled prompt and a durable clip.
    for shot in episode.all_shots():
        assert shot.keyframe_prompt, f"shot {shot.index} has no keyframe prompt"
        assert shot.video_prompt, f"shot {shot.index} has no video prompt"
        assert shot.video_asset_key, f"shot {shot.index} produced no clip"

    # Characters got reference sheets, and those references reached the shots.
    for character in context.bible.characters:
        assert character.reference_images, f"{character.name} has no reference sheet"
        assert character.canonical_reference

    assert summary["video_url"]
    path = await context.storage.local_path(episode.final_video_asset_key)
    info = await probe(path)
    kinds = {s["codec_type"] for s in info["streams"]}
    assert "video" in kinds and "audio" in kinds

    duration = float(info["format"]["duration"])
    assert duration > 20, "the cut is suspiciously short"
    # The assembled runtime must track what the pipeline planned.
    assert abs(duration - episode.planned_seconds()) / episode.planned_seconds() < 0.2

    assert ("pipeline", "completed") in events
    assert summary["cost"]["spent_usd"] == 0.0, "the offline engine must cost nothing"


@pytest.mark.slow
async def test_a_production_resumes_from_its_checkpoint():
    """Kill a run after the scene grid; the rerun must not rewrite the bible."""
    options = ProductionOptions(target_seconds=45, profile="offline", resolution="480p",
                                reference_images_per_character=1, generate_score=False,
                                critique=False)
    request = ProductionRequest(brief=BRIEF, options=options, job_id="job_resume_test")

    first = build_context(request)
    from nexus.pipeline.orchestrator import Orchestrator, build_stages

    stages = build_stages(request.brief, request.premise)
    partial = [s for s in stages if s.name in ("bible", "outline", "scene_grid")]
    await Orchestrator(first, partial).run()

    original_title = first.bible.title
    scene_count = len(first.episode.scenes)
    assert original_title and scene_count

    second = build_context(request)
    saved = await second.load_checkpoint()
    assert saved is not None, "no checkpoint was written"

    second.plan = saved
    assert second.bible.title == original_title
    assert len(second.episode.scenes) == scene_count

    # Re-running the writing stages must be a no-op, not a rewrite.
    await Orchestrator(second, partial).run()
    assert second.bible.title == original_title
    assert len(second.episode.scenes) == scene_count
