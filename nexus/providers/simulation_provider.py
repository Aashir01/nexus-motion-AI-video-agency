"""The offline engine: a complete, zero-cost, zero-key generation backend.

Why this exists
---------------
A long-form drama pipeline that only works when six paid API keys are present is
untestable, undemoable, and unpleasant to develop against.  The simulation
provider implements every modality locally:

* **text**  – deterministic, schema-valid story documents (`_simgen`)
* **image** – readable shot-slate plates drawn with the built-in PNG writer
* **video** – a Ken Burns move over the keyframe, muxed by ffmpeg
* **tts**   – speech-shaped placeholder audio at exactly the right duration
* **music** – a procedural ambient bed

An episode produced this way is watchable as an animatic, times out correctly,
and exercises every line of the assembly path.  It is also the router's final
fallback, which is what guarantees a job never dies just because a provider is
down.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from nexus.config import settings
from nexus.providers._simgen import generate_from_schema
from nexus.providers.base import Provider
from nexus.providers.registry import register
from nexus.render.pngwriter import Canvas, palette_from_seed
from nexus.render.wavsynth import (
    ambience_noise,
    ambient_bed,
    pitch_for_voice,
    voice_placeholder,
)
from nexus.routing.catalog import ModelSpec
from nexus.routing.types import (
    AudioRequest,
    ImageRequest,
    LLMRequest,
    Modality,
    ModelResponse,
    TTSRequest,
    Usage,
    VideoRequest,
)
from nexus.util.errors import ProviderError
from nexus.util.logging import get_logger

log = get_logger(__name__)

_DIMS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
         "4:5": (896, 1120), "2.39:1": (1280, 536)}


def _seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:6], "big")


class SimulationProvider(Provider):
    id = "simulation"

    # ── text ─────────────────────────────────────────────────────────────
    async def generate_text(self, spec: ModelSpec, req: LLMRequest) -> ModelResponse:
        await asyncio.sleep(0)  # stay a real coroutine for the router's timing
        if not req.json_schema:
            text = (
                "[offline engine] "
                + (req.prompt.strip().splitlines() or ["(no prompt)"])[0][:200]
            )
            return ModelResponse(
                provider=self.id, model_id=spec.id, modality=req.modality, text=text,
                usage=Usage(input_tokens=len(req.prompt) // 4, output_tokens=len(text) // 4),
            )

        document = generate_from_schema(
            req.json_schema, f"{req.system}\n{req.prompt}", seed=_seed(req.label, req.prompt[:2000])
        )
        import json

        text = json.dumps(document, ensure_ascii=False)
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=req.modality, text=text,
            parsed=document,
            usage=Usage(input_tokens=len(req.prompt) // 4, output_tokens=len(text) // 4),
        )

    # ── image ────────────────────────────────────────────────────────────
    async def generate_image(self, spec: ModelSpec, req: ImageRequest) -> ModelResponse:
        width, height = _DIMS.get(req.aspect_ratio, (1280, 720))
        seed = _seed(req.label, req.prompt[:400])
        png = await asyncio.to_thread(self._draw_plate, req, width, height, seed)
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=Modality.IMAGE,
            data=png, content_type="image/png", usage=Usage(images=1),
        )

    @staticmethod
    def _draw_plate(req: ImageRequest, width: int, height: int, seed: int) -> bytes:
        """Draw a legible shot slate.

        Everything sits inside a 10% title-safe margin, because the offline video
        synth applies a Ken Burns push and anything near the edge gets cropped.
        """
        top, bottom, accent = palette_from_seed(seed)
        canvas = Canvas(width, height, bottom)
        canvas.vertical_gradient(top, bottom)
        canvas.rect(0, int(height * 0.60), width, 2, tuple(min(255, c + 40) for c in accent))  # type: ignore[arg-type]

        margin_x = int(width * 0.11)
        safe_w = width - margin_x * 2
        scale = max(1, safe_w // 460)
        advance = 6 * scale
        max_chars = max(18, safe_w // advance)

        canvas.rect(margin_x, int(height * 0.13), safe_w, 3, accent)
        canvas.text(req.label.upper()[:max_chars], margin_x, int(height * 0.09), accent, scale)

        # The prompt is long by design; the slate shows the head of it, which is
        # where the identity and staging clauses live.
        canvas.wrap_text(
            req.prompt, margin_x, int(height * 0.21), max_chars=max_chars,
            color=(214, 220, 230), scale=scale, line_gap=max(4, scale * 3), max_lines=6,
        )

        footer = f"{len(req.reference_image_urls)} REFS  {req.aspect_ratio}  OFFLINE PLATE"
        canvas.text(footer, margin_x, int(height * 0.88), (150, 158, 172), max(1, scale - 1))
        canvas.vignette(0.45)
        return canvas.to_png()

    # ── video ────────────────────────────────────────────────────────────
    async def generate_video(self, spec: ModelSpec, req: VideoRequest) -> ModelResponse:
        duration = max(1.0, min(req.duration_s, spec.max_duration_s or req.duration_s))
        width, height = _DIMS.get(req.aspect_ratio, (1280, 720))
        if not shutil.which(settings.ffmpeg_binary):
            raise ProviderError(
                "ffmpeg is required for offline video synthesis but was not found on PATH",
                provider=self.id, model=spec.provider_model, retryable=False,
            )

        seed = _seed(req.label, req.prompt[:400])
        source_png = None
        if req.first_frame_url:
            source_png = await self._maybe_read_local(req.first_frame_url)
        if source_png is None:
            source_png = self._draw_plate(
                ImageRequest(prompt=req.prompt, label=req.label, aspect_ratio=req.aspect_ratio),
                width, height, seed,
            )

        data = await asyncio.to_thread(
            self._ken_burns, source_png, duration, width, height, req.fps, seed
        )
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=Modality.VIDEO, data=data,
            content_type="video/mp4", duration_s=duration, usage=Usage(seconds=duration),
        )

    @staticmethod
    async def _maybe_read_local(url: str) -> bytes | None:
        """Keyframes we produced ourselves are already on disk — reuse them."""
        try:
            if url.startswith("file://"):
                return Path(url[7:]).read_bytes()
            path = Path(url)
            if path.exists() and path.is_file():
                return path.read_bytes()
            from nexus.storage import get_storage

            storage = get_storage()
            marker = "/assets/"
            if marker in url:
                key = url.split(marker, 1)[1]
                return await storage.get_bytes(key)
        except Exception:  # a missing reference is not fatal — draw a fresh plate
            return None
        return None

    @staticmethod
    def _ken_burns(png: bytes, duration: float, width: int, height: int, fps: int, seed: int) -> bytes:
        """Animate a still with a slow push/pan so cuts read as shots, not slides."""
        with tempfile.TemporaryDirectory(prefix="nexus-sim-") as tmp:
            src = Path(tmp) / "frame.png"
            dst = Path(tmp) / "shot.mp4"
            src.write_bytes(png)

            frames = max(2, int(duration * fps))
            direction = seed % 4
            zoom_expr = {
                0: "min(zoom+0.0012,1.18)",
                1: "if(lte(zoom,1.0),1.18,max(1.001,zoom-0.0012))",
                2: "min(zoom+0.0008,1.12)",
                3: "1.06",
            }[direction]
            pan_x = {0: "iw/2-(iw/zoom/2)", 1: "iw/2-(iw/zoom/2)",
                     2: f"(iw-iw/zoom)*on/{frames}", 3: f"(iw-iw/zoom)*(1-on/{frames})"}[direction]
            pan_y = {0: "ih/2-(ih/zoom/2)", 1: "ih/2-(ih/zoom/2)",
                     2: "ih/2-(ih/zoom/2)", 3: f"(ih-ih/zoom)*on/{frames}"}[direction]

            vf = (
                f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
                f"crop={width*2}:{height*2},"
                f"zoompan=z='{zoom_expr}':x='{pan_x}':y='{pan_y}':"
                f"d={frames}:s={width}x{height}:fps={fps},"
                f"format=yuv420p"
            )
            cmd = [
                settings.ffmpeg_binary, "-y", "-loglevel", "error",
                "-loop", "1", "-i", str(src),
                "-vf", vf, "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=300)
            if proc.returncode != 0 or not dst.exists():
                raise ProviderError(
                    f"offline video render failed: {proc.stderr.decode()[:400]}",
                    provider="simulation", retryable=False,
                )
            return dst.read_bytes()

    # ── audio ────────────────────────────────────────────────────────────
    async def synthesize_speech(self, spec: ModelSpec, req: TTSRequest) -> ModelResponse:
        # ~2.6 words/second is a natural dramatic read.
        words = max(1, len(req.text.split()))
        duration = max(0.9, words / (2.6 * max(0.6, req.speed)))
        gender = "male" if (req.voice_id or "").lower().endswith(("guy", "male", "m")) else "neutral"
        pitch = pitch_for_voice(req.voice_id or "narrator", gender)
        data = await asyncio.to_thread(voice_placeholder, req.text, duration, pitch_hz=pitch)
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=Modality.TTS, data=data,
            content_type="audio/wav", duration_s=duration,
            usage=Usage(characters=len(req.text), seconds=duration),
        )

    async def generate_audio(self, spec: ModelSpec, req: AudioRequest) -> ModelResponse:
        seed = _seed(req.kind, req.prompt[:200])
        if req.kind in ("sfx", "ambience"):
            data = await asyncio.to_thread(ambience_noise, req.duration_s, seed=seed)
            modality = Modality.SFX
        else:
            data = await asyncio.to_thread(ambient_bed, req.duration_s, seed=seed)
            modality = Modality.MUSIC
        return ModelResponse(
            provider=self.id, model_id=spec.id, modality=modality, data=data,
            content_type="audio/wav", duration_s=req.duration_s,
            usage=Usage(seconds=req.duration_s),
        )


register(SimulationProvider.id, SimulationProvider)
