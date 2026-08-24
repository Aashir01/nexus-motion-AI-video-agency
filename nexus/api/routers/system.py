from __future__ import annotations

import time

from fastapi import APIRouter

from nexus.config import settings
from nexus.render.ffmpeg import ffmpeg_available
from nexus.routing.catalog import CATALOG, available_providers
from nexus.routing.types import Modality
from nexus.worker.bus import bus

router = APIRouter(tags=["system"])
_STARTED = time.time()


@router.get("/health")
async def health():
    """Liveness. Deliberately cheap — no database, no network."""
    return {"status": "ok", "service": "nexus-motion", "uptime_s": round(time.time() - _STARTED, 1)}


@router.get("/ready")
async def ready():
    """Readiness. Reports the truth about every dependency rather than a bare 200."""
    from nexus.db.session import get_engine

    checks: dict[str, dict] = {}

    try:
        from sqlalchemy import text

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    redis_client = await bus.redis()
    checks["queue"] = {"ok": True, "backend": "redis" if redis_client else "in-process"}
    checks["ffmpeg"] = {"ok": ffmpeg_available()}
    checks["storage"] = {"ok": True, "backend": settings.storage_backend}

    providers = available_providers()
    live = [name for name, ok in providers.items() if ok and name != "simulation"]
    checks["providers"] = {"ok": True, "configured": live,
                           "note": "simulation backstop always available"}

    ready_now = all(c.get("ok") for c in checks.values())
    return {"status": "ready" if ready_now else "degraded", "checks": checks}


@router.get("/capabilities")
async def capabilities():
    """What this deployment can actually do right now."""
    by_modality: dict[str, dict] = {}
    for modality in Modality:
        specs = [s for s in CATALOG.values() if s.modality is modality]
        usable = [s for s in specs if s.available()]
        by_modality[modality.value] = {
            "total": len(specs),
            "available": len(usable),
            "best_available": max(
                (s.id for s in usable), key=lambda i: int(CATALOG[i].quality), default=None
            ),
        }
    return {
        "app": settings.app_name,
        "environment": settings.environment,
        "ffmpeg": ffmpeg_available(),
        "billing_enabled": settings.billing_enabled,
        "modalities": by_modality,
        "providers": available_providers(),
    }
