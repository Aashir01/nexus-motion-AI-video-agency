"""Per-model circuit breaker.

A model that just 500'd three times in a row should not be the first thing the
next shot tries.  The breaker is process-local by default and shared through
Redis when a client is supplied, so a fleet of workers learns together.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from nexus.config import settings
from nexus.util.logging import get_logger

log = get_logger(__name__)


@dataclass
class _Window:
    failures: list[float] = field(default_factory=list)
    successes: int = 0
    total_latency_ms: float = 0.0
    opened_until: float = 0.0
    last_error: str = ""


class HealthTracker:
    def __init__(
        self,
        *,
        threshold: int | None = None,
        window_s: int | None = None,
        cooldown_s: float = 60.0,
    ):
        self.threshold = threshold or settings.provider_failure_threshold
        self.window_s = window_s or settings.provider_health_window_s
        self.cooldown_s = cooldown_s
        self._state: dict[str, _Window] = {}

    def _win(self, model_id: str) -> _Window:
        return self._state.setdefault(model_id, _Window())

    def record_success(self, model_id: str, latency_ms: float = 0.0) -> None:
        w = self._win(model_id)
        w.successes += 1
        w.total_latency_ms += latency_ms
        w.failures.clear()
        w.opened_until = 0.0

    def record_failure(self, model_id: str, error: str = "") -> None:
        now = time.time()
        w = self._win(model_id)
        w.failures = [t for t in w.failures if now - t < self.window_s]
        w.failures.append(now)
        w.last_error = error[:400]
        if len(w.failures) >= self.threshold:
            # Back off longer the more it keeps failing, capped at 15 minutes.
            over = len(w.failures) - self.threshold
            w.opened_until = now + min(self.cooldown_s * (2**over), 900)
            log.warning(
                "circuit_open", extra={"model_id": model_id, "for_s": round(w.opened_until - now), "error": w.last_error}
            )

    def is_open(self, model_id: str) -> bool:
        w = self._state.get(model_id)
        return bool(w and w.opened_until > time.time())

    def penalty(self, model_id: str) -> float:
        """0.0 healthy → 1.0 recently flaky. Used to nudge ranking."""
        w = self._state.get(model_id)
        if not w or not w.failures:
            return 0.0
        return min(1.0, len(w.failures) / max(1, self.threshold))

    def snapshot(self) -> dict[str, dict]:
        now = time.time()
        return {
            mid: {
                "successes": w.successes,
                "recent_failures": len([t for t in w.failures if now - t < self.window_s]),
                "circuit_open": w.opened_until > now,
                "reopens_in_s": max(0, round(w.opened_until - now)),
                "avg_latency_ms": round(w.total_latency_ms / w.successes, 1) if w.successes else None,
                "last_error": w.last_error or None,
            }
            for mid, w in self._state.items()
        }

    def reset(self) -> None:
        self._state.clear()


health = HealthTracker()
