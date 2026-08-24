"""The router is the piece most likely to silently misbehave, so it gets the
most tests: capability filtering, budget, fallback and profile ceilings."""
from __future__ import annotations

import pytest

from nexus.providers.base import Provider
from nexus.providers.registry import register
from nexus.routing.catalog import CATALOG, list_specs
from nexus.routing.ledger import CostLedger
from nexus.routing.router import ModelRouter, Requirements
from nexus.routing.types import (
    ImageRequest,
    LLMRequest,
    Modality,
    Tier,
    VideoRequest,
)
from nexus.util.errors import BudgetExceededError, NoRouteError, ProviderError


class _AlwaysFails(Provider):
    id = "test_broken"

    async def generate_text(self, spec, req):
        raise ProviderError("upstream is on fire", provider=self.id, model=spec.provider_model)

    async def generate_image(self, spec, req):
        raise ProviderError("upstream is on fire", provider=self.id, model=spec.provider_model)


register("test_broken", _AlwaysFails)


def test_catalog_covers_every_modality():
    for modality in Modality:
        assert list_specs(modality), f"no models registered for {modality}"


def test_simulation_backstop_exists_for_every_generative_modality():
    """Without this the 'a job always finishes' guarantee is a lie."""
    for modality in (Modality.TEXT, Modality.IMAGE, Modality.VIDEO, Modality.TTS, Modality.MUSIC):
        offline = [s for s in list_specs(modality) if s.provider == "simulation"]
        assert offline, f"no offline fallback for {modality}"
        assert all(s.available() for s in offline)


def test_profile_ceiling_is_enforced():
    router = ModelRouter(profile="free")
    decision = router.select("shot_video", Requirements(needs_text_to_video=True))
    assert all(CATALOG[c.id].tier is Tier.FREE for c in decision.candidates)


def test_premium_profile_prefers_identity_capable_video():
    router = ModelRouter(profile="premium", allowed_models=set(CATALOG))
    decision = router.select("shot_video", Requirements(needs_image_to_video=True))
    assert decision.candidates, "premium profile resolved no video model"


def test_requirements_filter_out_incapable_models():
    reqs = Requirements(needs_reference_images=True)
    for spec in list_specs(Modality.VIDEO):
        assert reqs.matches(spec) == spec.supports_reference_images


def test_requirements_relax_in_priority_order():
    """Identity conditioning must be the last thing surrendered."""
    reqs = Requirements(needs_reference_images=True, needs_native_audio=True, min_quality=4)
    order = []
    current = reqs
    while (current := current.relaxed()) is not None:
        order.append(current)
    assert not order[0].needs_native_audio
    assert order[0].needs_reference_images, "references dropped too early"
    assert not order[-1].needs_reference_images


def test_duration_clamping_matches_model_limits():
    kling = CATALOG["fal/kling-3.0-pro"]
    assert kling.clamp_duration(30) == kling.max_duration_s
    assert kling.clamp_duration(1) == kling.min_duration_s
    assert not kling.supports_duration(30)


@pytest.mark.asyncio
async def test_router_falls_through_to_a_working_model():
    router = ModelRouter(profile="offline")
    response = await router.text("screenwriter", LLMRequest(
        system="test", prompt="test", json_schema={"type": "object",
                                                   "properties": {"a": {"type": "string"}},
                                                   "required": ["a"]},
    ))
    assert response.parsed["a"]
    assert response.provider == "simulation"


@pytest.mark.asyncio
async def test_budget_stops_spending_before_the_call():
    router = ModelRouter(profile="premium", ledger=CostLedger(budget_usd=0.0001),
                         allow_simulation=False)
    with pytest.raises((BudgetExceededError, NoRouteError)):
        await router.video("shot_video", VideoRequest(prompt="x", duration_s=5))


@pytest.mark.asyncio
async def test_failed_model_opens_its_circuit_and_is_demoted():
    from nexus.routing.health import HealthTracker

    tracker = HealthTracker(threshold=2, cooldown_s=30)
    tracker.record_failure("fal/kling-3.0-pro", "500")
    tracker.record_failure("fal/kling-3.0-pro", "500")
    assert tracker.is_open("fal/kling-3.0-pro")
    assert tracker.penalty("fal/kling-3.0-pro") == 1.0
    tracker.record_success("fal/kling-3.0-pro", 100)
    assert not tracker.is_open("fal/kling-3.0-pro")


@pytest.mark.asyncio
async def test_ledger_records_cost_per_role():
    router = ModelRouter(profile="offline")
    await router.image("keyframe", ImageRequest(prompt="a room", label="t"))
    summary = router.ledger.summary()
    assert summary["calls"] == 1
    assert "keyframe" in summary["by_role"]


def test_no_route_raises_a_useful_error():
    router = ModelRouter(profile="offline", allowed_models=set(), allow_simulation=False)
    with pytest.raises(NoRouteError) as exc:
        router.select("shot_video")
    assert "hint" in exc.value.detail


def test_every_spec_has_pricing_or_is_free():
    for spec in CATALOG.values():
        if spec.tier is Tier.FREE:
            continue
        has_price = any([spec.usd_per_1m_input, spec.usd_per_1m_output, spec.usd_per_image,
                         spec.usd_per_second, spec.usd_per_1k_chars])
        assert has_price, f"{spec.id} is a paid model with no price"
