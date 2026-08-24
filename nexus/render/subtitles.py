"""Subtitle generation from the timed cut."""
from __future__ import annotations

from nexus.story.schemas import Episode, SeriesBible
from nexus.util.timing import srt_timestamp


def build_srt(episode: Episode, bible: SeriesBible, *, name_prefix: bool = True) -> str:
    """Walk the assembled timeline and emit an SRT keyed to real shot offsets."""
    blocks: list[str] = []
    index = 1
    clock = 0.0

    for scene in episode.scenes:
        for shot in scene.shots:
            duration = shot.actual_seconds or shot.planned_seconds()
            if not shot.video_asset_key:
                continue
            offset = clock
            for line in shot.dialogue:
                start = offset + line.start_offset_s
                end = start + max(0.7, line.duration())
                speaker = bible.character(line.character_id)
                label = ""
                if name_prefix and speaker:
                    label = f"{speaker.name.upper()}: "
                elif name_prefix and line.character_id == "NARRATOR":
                    label = "NARRATOR: "
                blocks.append(
                    f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(min(end, offset + duration))}\n"
                    f"{label}{_wrap(line.text)}\n"
                )
                index += 1
            clock += duration

    return "\n".join(blocks)


def _wrap(text: str, width: int = 42) -> str:
    words, line, out = text.split(), "", []
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            out.append(line)
            line = word
        else:
            line = candidate
    if line:
        out.append(line)
    return "\n".join(out[:2]) if len(out) <= 2 else "\n".join([out[0], " ".join(out[1:])[:width]])
