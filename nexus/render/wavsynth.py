"""Procedural WAV synthesis for the offline engine.

Real TTS is replaced by a low-level, per-character-pitched "voice placeholder"
that follows the word rhythm of the line.  That is deliberately more useful than
silence: you can hear where dialogue sits, confirm timing and lip-flap pacing,
and tell two characters apart, all without spending a cent.
"""
from __future__ import annotations

import array
import io
import math
import random
import wave

SAMPLE_RATE = 44100


def _write_wav(samples: array.array, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _clamp16(v: float) -> int:
    return max(-32768, min(32767, int(v)))


def silence(duration_s: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    return _write_wav(array.array("h", [0] * int(duration_s * sample_rate)), sample_rate)


def voice_placeholder(
    text: str,
    duration_s: float,
    *,
    pitch_hz: float = 130.0,
    amplitude: float = 0.16,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    """A speech-shaped tone: one syllable-ish burst per word, correct total length."""
    total = max(1, int(duration_s * sample_rate))
    out = array.array("h", [0] * total)
    words = [w for w in text.split() if w] or ["..."]

    cursor = 0
    per_word = total / len(words)
    rng = random.Random(hash(text) & 0xFFFF)

    for _word in words:
        span = int(per_word)
        voiced = int(span * 0.78)
        # Pitch drifts a little per word so it reads as speech, not a test tone.
        f0 = pitch_hz * (1.0 + rng.uniform(-0.06, 0.06))
        f0 *= 1.0 + 0.10 * math.sin(cursor / max(1, total) * math.pi * 2)
        for i in range(voiced):
            if cursor + i >= total:
                break
            t = i / sample_rate
            env = math.sin(math.pi * i / max(1, voiced)) ** 0.6
            sample = (
                math.sin(2 * math.pi * f0 * t) * 0.55
                + math.sin(2 * math.pi * f0 * 2 * t) * 0.22
                + math.sin(2 * math.pi * f0 * 3 * t) * 0.10
            )
            out[cursor + i] = _clamp16(sample * env * amplitude * 32767)
        cursor += span
        if cursor >= total:
            break

    return _write_wav(out, sample_rate)


def ambient_bed(
    duration_s: float,
    *,
    seed: int = 0,
    intensity: float = 0.5,
    amplitude: float = 0.10,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    """A slow evolving pad — stands in for a score bed under a whole episode."""
    total = max(1, int(duration_s * sample_rate))
    out = array.array("h", [0] * total)
    rng = random.Random(seed)
    root = rng.choice([55.0, 61.74, 65.41, 73.42, 82.41])
    # Minor-ish stack keeps it neutral-tense rather than cheerful.
    partials = [(1.0, 0.42), (1.5, 0.20), (2.0, 0.16), (2.4, 0.10), (3.0, 0.07)]
    lfo1, lfo2 = rng.uniform(0.02, 0.05), rng.uniform(0.007, 0.02)

    step = 4  # synthesise every 4th sample and interpolate: 4x faster, inaudible difference
    prev = 0.0
    for i in range(0, total, step):
        t = i / sample_rate
        swell = 0.55 + 0.45 * math.sin(2 * math.pi * lfo1 * t) * math.sin(2 * math.pi * lfo2 * t)
        value = sum(
            math.sin(2 * math.pi * root * mult * t + mult) * gain for mult, gain in partials
        )
        value *= swell * (0.5 + intensity * 0.5)
        sample = _clamp16(value * amplitude * 32767)
        for j in range(step):
            if i + j < total:
                out[i + j] = _clamp16(prev + (sample - prev) * (j / step))
        prev = float(sample)

    _apply_fades(out, sample_rate)
    return _write_wav(out, sample_rate)


def ambience_noise(
    duration_s: float,
    *,
    seed: int = 0,
    amplitude: float = 0.07,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    """Filtered noise: room tone, rain, traffic — whatever the scene claims."""
    total = max(1, int(duration_s * sample_rate))
    out = array.array("h", [0] * total)
    rng = random.Random(seed)
    state = 0.0
    for i in range(total):
        white = rng.uniform(-1.0, 1.0)
        state = state * 0.97 + white * 0.03          # one-pole low pass
        out[i] = _clamp16(state * amplitude * 4 * 32767)
    _apply_fades(out, sample_rate)
    return _write_wav(out, sample_rate)


def _apply_fades(samples: array.array, sample_rate: int, fade_s: float = 0.75) -> None:
    n = min(int(fade_s * sample_rate), len(samples) // 2)
    for i in range(n):
        g = i / n
        samples[i] = _clamp16(samples[i] * g)
        samples[-1 - i] = _clamp16(samples[-1 - i] * g)


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or SAMPLE_RATE)


def pitch_for_voice(voice_id: str, gender: str = "neutral") -> float:
    base = {"male": 108.0, "female": 196.0, "neutral": 150.0}.get(gender, 150.0)
    spread = (sum(ord(c) for c in voice_id) % 40) - 20
    return base + spread
