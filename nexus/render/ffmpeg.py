"""Thin async wrapper around ffmpeg/ffprobe."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from nexus.config import settings
from nexus.util.errors import RenderError
from nexus.util.logging import get_logger

log = get_logger(__name__)


def ffmpeg_available() -> bool:
    return bool(shutil.which(settings.ffmpeg_binary) and shutil.which(settings.ffprobe_binary))


def require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise RenderError(
            "ffmpeg and ffprobe are required for rendering but were not found on PATH. "
            "Install them (apt-get install ffmpeg) or use the provided container image."
        )


async def run(args: list[str], *, timeout: float = 1800.0, what: str = "ffmpeg") -> None:
    cmd = [settings.ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        raise RenderError(f"{what} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        detail = (stderr or b"").decode(errors="replace")[-1500:]
        raise RenderError(f"{what} failed (exit {proc.returncode}): {detail}")


async def probe(path: str | Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        settings.ffprobe_binary, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RenderError(f"ffprobe failed for {path}: {(stderr or b'').decode()[:400]}")
    return json.loads(stdout or b"{}")


async def probe_duration(path: str | Path) -> float:
    info = await probe(path)
    duration = (info.get("format") or {}).get("duration")
    if duration:
        return float(duration)
    for stream in info.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    return 0.0


async def has_audio_stream(path: str | Path) -> bool:
    info = await probe(path)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


RESOLUTIONS = {
    "480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080),
    "1440p": (2560, 1440), "4k": (3840, 2160),
}


def frame_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    width, height = RESOLUTIONS.get(resolution, (1920, 1080))
    ratios = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 0.8, "2.39:1": 2.39}
    ratio = ratios.get(aspect_ratio, 16 / 9)
    if ratio >= 1:
        height = int(round(width / ratio / 2) * 2)
    else:
        width, height = height, width
        width = int(round(height * ratio / 2) * 2)
    return width, height
