"""Episode assembly.

Four passes, each idempotent and inspectable:

1. **Shot normalise** — every clip is conformed to one resolution, fps and pixel
   format, retimed to the duration its dialogue actually needs, and given a
   dialogue track mixed at the right offsets.
2. **Concat** — shots are joined; a shot marked `crossfade` overlaps its
   predecessor instead of cutting.
3. **Mix** — the score is laid under the whole episode and side-chain ducked by
   the dialogue bus, so music never fights a line.
4. **Finish** — subtitles (soft or burned), thumbnail, faststart.

ffmpeg is used directly rather than through MoviePy: a 12-minute, 130-shot cut
is exactly the case where MoviePy's in-memory model falls over.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from nexus.config import settings
from nexus.render import ffmpeg
from nexus.render.subtitles import build_srt
from nexus.story.schemas import Episode, SeriesBible, Shot, TransitionType
from nexus.util.errors import RenderError
from nexus.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class RenderRequest:
    episode: Episode
    bible: SeriesBible
    workdir: Path
    shot_video_paths: dict[int, Path]         # shot.index → local clip
    dialogue_paths: dict[tuple[int, int], Path]   # (shot.index, line index) → local audio
    score_path: Path | None = None
    resolution: str = "1080p"
    aspect_ratio: str = "16:9"
    fps: int = 24
    burn_subtitles: bool = False
    write_subtitles: bool = True


@dataclass(slots=True)
class RenderResult:
    video_path: Path
    thumbnail_path: Path | None
    subtitle_path: Path | None
    duration_s: float
    shots_used: int
    shots_skipped: int


async def render_episode(req: RenderRequest) -> RenderResult:
    ffmpeg.require_ffmpeg()
    width, height = ffmpeg.frame_size(req.resolution, req.aspect_ratio)
    stage_dir = req.workdir / "normalised"
    stage_dir.mkdir(parents=True, exist_ok=True)

    prepared: list[tuple[Shot, Path, float]] = []
    skipped = 0
    clock = 0.0

    for scene in req.episode.scenes:
        for shot in scene.shots:
            source = req.shot_video_paths.get(shot.index)
            if not source or not source.exists():
                skipped += 1
                continue
            target = _shot_duration(shot)
            out = stage_dir / f"shot_{shot.index:04d}.mp4"
            try:
                await _normalise_shot(shot, source, out, req, width, height, target)
            except RenderError as exc:
                log.warning("shot_normalise_failed",
                            extra={"shot": shot.index, "error": str(exc)[:300]})
                skipped += 1
                continue
            actual = await ffmpeg.probe_duration(out)
            shot.actual_seconds = actual or target
            prepared.append((shot, out, shot.actual_seconds))
            clock += shot.actual_seconds

    if not prepared:
        raise RenderError(
            "no usable shots to assemble — every clip was missing or failed to conform"
        )

    body = req.workdir / "body.mp4"
    await _concat(prepared, body, req, width, height)

    mixed = req.workdir / "mixed.mp4"
    if req.score_path and req.score_path.exists():
        await _mix_score(body, req.score_path, mixed, req.bible)
    else:
        shutil.copyfile(body, mixed)

    subtitle_path: Path | None = None
    if req.write_subtitles or req.burn_subtitles:
        srt = build_srt(req.episode, req.bible)
        if srt.strip():
            subtitle_path = req.workdir / "subtitles.srt"
            subtitle_path.write_text(srt, encoding="utf-8")

    final = req.workdir / "episode.mp4"
    await _finish(mixed, final, subtitle_path, req)

    thumbnail = await _thumbnail(final, req.workdir / "thumbnail.jpg")
    duration = await ffmpeg.probe_duration(final)
    req.episode.actual_seconds = duration

    return RenderResult(
        video_path=final, thumbnail_path=thumbnail, subtitle_path=subtitle_path,
        duration_s=duration, shots_used=len(prepared), shots_skipped=skipped,
    )


def _shot_duration(shot: Shot) -> float:
    """A shot lasts as long as its dialogue plus breathing room, never less than
    what the director asked for."""
    dialogue = shot.dialogue_seconds()
    if dialogue:
        return max(shot.target_seconds, dialogue + 0.9)
    return max(2.0, shot.target_seconds)


async def _normalise_shot(
    shot: Shot, source: Path, out: Path, req: RenderRequest,
    width: int, height: int, target: float,
) -> None:
    """Conform one clip and lay its dialogue onto it."""
    source_duration = await ffmpeg.probe_duration(source) or target

    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={req.fps},format=yuv420p"
    )
    # If the clip is shorter than the line it carries, hold on the last frame
    # rather than speeding up the performance.
    args: list[str] = []
    if source_duration + 0.12 < target:
        args += ["-i", str(source)]
        video_filter = f"{video_filter},tpad=stop_mode=clone:stop_duration={target - source_duration:.3f}"
    else:
        args += ["-i", str(source)]

    audio_inputs: list[str] = []
    audio_filters: list[str] = []
    offset = 0.35 if shot.dialogue else 0.0
    for i, line in enumerate(shot.dialogue):
        path = req.dialogue_paths.get((shot.index, i))
        if not path or not path.exists():
            continue
        line.start_offset_s = offset
        args += ["-i", str(path)]
        idx = len(audio_inputs) + 1
        delay_ms = int(offset * 1000)
        audio_filters.append(
            f"[{idx}:a]aresample=48000,adelay={delay_ms}|{delay_ms},"
            f"apad=whole_dur={target:.3f}[d{idx}]"
        )
        audio_inputs.append(f"[d{idx}]")
        offset += max(0.6, line.duration()) + 0.28

    fade_v, fade_a = _transition_filters(shot, target)
    if fade_v:
        video_filter = f"{video_filter},{fade_v}"

    filter_parts = [f"[0:v]{video_filter}[v]"]
    if audio_inputs:
        filter_parts += audio_filters
        if len(audio_inputs) == 1:
            filter_parts.append(f"{audio_inputs[0]}anull[amix_out]")
        else:
            filter_parts.append(
                f"{''.join(audio_inputs)}amix=inputs={len(audio_inputs)}:"
                f"normalize=0:duration=longest[amix_out]"
            )
        filter_parts.append(f"[amix_out]{fade_a or 'anull'}[a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        filter_parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{target:.3f}[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]

    await ffmpeg.run(
        [
            *args,
            "-filter_complex", ";".join(filter_parts),
            *maps,
            "-t", f"{target:.3f}",
            "-c:v", "libx264", "-preset", settings.render_preset, "-crf", str(settings.render_crf),
            "-pix_fmt", "yuv420p", "-r", str(req.fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest", str(out),
        ],
        timeout=600.0, what=f"normalise shot {shot.index}",
    )


async def _concat(
    prepared: list[tuple[Shot, Path, float]], out: Path,
    req: RenderRequest, width: int, height: int,
) -> None:
    """Join the shots with the concat demuxer.

    Every clip was conformed to identical codec parameters in pass 1, so this is
    a stream copy: a 130-shot episode joins in seconds instead of re-encoding for
    half an hour. Dissolves are baked into the shots themselves (see
    `_transition_filters`) rather than built as an xfade graph — at this shot
    count an xfade chain is both slow and fragile, and a 0.4s dip reads the same
    on screen.
    """
    listing = req.workdir / "concat.txt"
    listing.write_text(
        "".join(f"file '{path.as_posix()}'\n" for _, path, _ in prepared), encoding="utf-8"
    )
    await ffmpeg.run(
        ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
         "-movflags", "+faststart", str(out)],
        timeout=1800.0, what="concat",
    )


def _transition_filters(shot: Shot, duration: float) -> tuple[str, str]:
    """Bake the shot's transition-in as a fade at the head of the clip.

    A cut needs nothing. A dissolve or a fade-from-black gets a short ramp on
    both picture and sound, which survives a stream-copy concat intact.
    """
    ramp = {
        TransitionType.CUT: 0.0,
        TransitionType.MATCH_CUT: 0.0,
        TransitionType.WHIP_PAN: 0.12,
        TransitionType.CROSSFADE: 0.4,
        TransitionType.FADE_FROM_BLACK: 0.8,
        TransitionType.FADE_TO_BLACK: 0.4,
    }.get(shot.transition_in, 0.0)
    if ramp <= 0 or duration <= ramp * 2.5:
        return "", ""
    video = f"fade=t=in:st=0:d={ramp:.2f}"
    audio = f"afade=t=in:st=0:d={min(ramp, 0.3):.2f}"
    if shot.transition_in is TransitionType.FADE_TO_BLACK:
        video += f",fade=t=out:st={duration - ramp:.2f}:d={ramp:.2f}"
        audio += f",afade=t=out:st={duration - ramp:.2f}:d={ramp:.2f}"
    return video, audio


async def _mix_score(body: Path, score: Path, out: Path, bible: SeriesBible) -> None:
    """Lay the bed under the cut and duck it against the dialogue bus."""
    style = bible.audio_style
    music_gain = 10 ** (style.music_bed_gain_db / 20)
    duck_ratio = max(2.0, min(12.0, abs(style.duck_music_under_dialogue_db)))

    filter_complex = (
        "[0:a]aformat=channel_layouts=stereo,asplit=2[dial][side];"
        f"[1:a]aformat=channel_layouts=stereo,aloop=loop=-1:size=2e9,volume={music_gain:.4f}[bed];"
        f"[bed][side]sidechaincompress=threshold=0.03:ratio={duck_ratio:.1f}:"
        "attack=25:release=450:makeup=1[ducked];"
        "[dial][ducked]amix=inputs=2:duration=first:normalize=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=11[mix]"
    )
    await ffmpeg.run(
        ["-i", str(body), "-i", str(score),
         "-filter_complex", filter_complex,
         "-map", "0:v", "-map", "[mix]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", str(out)],
        timeout=1800.0, what="score mix",
    )


async def _finish(source: Path, out: Path, subtitles: Path | None, req: RenderRequest) -> None:
    if req.burn_subtitles and subtitles and subtitles.exists():
        style = ("FontName=DejaVu Sans,FontSize=20,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H90000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=42")
        await ffmpeg.run(
            ["-i", str(source),
             "-vf", f"subtitles={_escape(subtitles)}:force_style='{style}'",
             "-c:v", "libx264", "-preset", settings.render_preset, "-crf", str(settings.render_crf),
             "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)],
            timeout=2400.0, what="burn subtitles",
        )
        return

    if subtitles and subtitles.exists():
        # Soft subtitles: switchable in any player, zero quality cost.
        await ffmpeg.run(
            ["-i", str(source), "-i", str(subtitles),
             "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text",
             "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", str(out)],
            timeout=900.0, what="mux subtitles",
        )
        return

    shutil.copyfile(source, out)


async def _thumbnail(video: Path, out: Path) -> Path | None:
    try:
        duration = await ffmpeg.probe_duration(video)
        await ffmpeg.run(
            ["-ss", f"{max(1.0, duration * 0.18):.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            timeout=120.0, what="thumbnail",
        )
        return out if out.exists() else None
    except RenderError:
        return None


def _escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
