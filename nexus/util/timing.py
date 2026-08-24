from __future__ import annotations

import time
from contextlib import contextmanager


class Stopwatch:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.elapsed_ms = 0.0

    def stop(self) -> float:
        self.elapsed_ms = (time.perf_counter() - self.started) * 1000
        return self.elapsed_ms


@contextmanager
def stopwatch():
    sw = Stopwatch()
    try:
        yield sw
    finally:
        sw.stop()


def hhmmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = round((seconds - int(seconds)) * 1000)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
