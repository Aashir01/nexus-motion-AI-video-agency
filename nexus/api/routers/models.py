"""Model catalog, routing profiles and provider health — the transparency surface."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from nexus.api.deps import Principal, get_principal
from nexus.routing.catalog import CATALOG, available_providers, list_specs
from nexus.routing.health import health
from nexus.routing.profiles import PROFILES, ROLE_MODALITY
from nexus.routing.router import build_router
from nexus.routing.types import Modality, Tier
from nexus.util.errors import NotFoundError

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
async def list_models(
    modality: str | None = Query(None, description="text | image | video | tts | music | sfx"),
    tier: str | None = Query(None, description="free | budget | standard | premium | flagship"),
    available_only: bool = False,
):
    """Every model the platform can route to, with live availability and pricing."""
    specs = list_specs(
        Modality(modality) if modality else None,
        tier=Tier(tier) if tier else None,
        available_only=available_only,
    )
    return {
        "count": len(specs),
        "providers": available_providers(),
        "models": [s.to_public_dict() for s in specs],
    }


@router.get("/{model_id:path}/detail")
async def model_detail(model_id: str):
    spec = CATALOG.get(model_id)
    if spec is None:
        raise NotFoundError(f"unknown model {model_id!r}")
    return spec.to_public_dict() | {"health": health.snapshot().get(model_id, {})}


@router.get("/routing/profiles")
async def list_profiles(principal: Principal = Depends(get_principal)):
    """Profiles this account can select, plus the resolved chain for each role."""
    allowed = set(principal.plan.routing_profiles)
    out = []
    for name, profile in PROFILES.items():
        model_router = build_router(profile=name)
        out.append({
            "name": name,
            "label": profile.label,
            "description": profile.description,
            "max_tier": profile.max_tier.value,
            "available_to_plan": name in allowed,
            "resolved_chain": model_router.plan(),
        })
    return {"profiles": out, "roles": {r: m.value for r, m in ROLE_MODALITY.items()}}


@router.get("/routing/health")
async def provider_health(principal: Principal = Depends(get_principal)):
    """Circuit-breaker state per model. Explains why a job fell back."""
    return {"providers": available_providers(), "models": health.snapshot()}
