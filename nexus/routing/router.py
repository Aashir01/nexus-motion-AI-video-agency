"""The model router.

Every generation call in the platform goes through `ModelRouter`.  It:

  1. builds a candidate list for the (role, modality, capability) triple,
  2. orders it by profile preference → quality → price → recent health,
  3. enforces the job's remaining budget before spending anything,
  4. executes with fallback: if the premium model 429s or 500s, the next
     candidate takes the shot, all the way down to the offline simulator,
  5. records what it spent and how it behaved.

That last step is what makes "best paid models down to free models" a routing
policy rather than a config choice: a Flagship job that exhausts Veo capacity
degrades to Kling, then Wan, then the simulator, and still ships an episode.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from nexus.providers.registry import get_provider
from nexus.routing.catalog import CATALOG, ModelSpec, list_specs
from nexus.routing.health import HealthTracker
from nexus.routing.health import health as global_health
from nexus.routing.ledger import CostLedger, LedgerEntry
from nexus.routing.profiles import ROLE_MODALITY, RoutingProfile, get_profile
from nexus.routing.types import (
    AudioRequest,
    ImageRequest,
    LLMRequest,
    Modality,
    ModelResponse,
    Tier,
    TTSRequest,
    VideoRequest,
)
from nexus.util.errors import (
    BudgetExceededError,
    NexusError,
    NoRouteError,
    ProviderError,
)
from nexus.util.logging import get_logger
from nexus.util.timing import Stopwatch

log = get_logger(__name__)


@dataclass(slots=True)
class Requirements:
    """Hard capability filters. A candidate failing any of these is dropped."""

    needs_reference_images: bool = False
    needs_image_to_video: bool = False
    needs_text_to_video: bool = False
    needs_image_edit: bool = False
    needs_native_audio: bool = False
    needs_first_last_frame: bool = False
    needs_json_schema: bool = False
    needs_vision: bool = False
    duration_s: float | None = None
    min_quality: int = 0
    exclude: set[str] = field(default_factory=set)

    def matches(self, spec: ModelSpec) -> bool:
        if spec.id in self.exclude:
            return False
        if int(spec.quality) < self.min_quality:
            return False
        checks = (
            (self.needs_reference_images, spec.supports_reference_images),
            (self.needs_image_to_video, spec.supports_image_to_video),
            (self.needs_text_to_video, spec.supports_text_to_video),
            (self.needs_image_edit, spec.supports_image_edit),
            (self.needs_native_audio, spec.supports_native_audio),
            (self.needs_first_last_frame, spec.supports_first_last_frame),
            (self.needs_json_schema, spec.supports_json_schema),
            (self.needs_vision, spec.supports_vision),
        )
        if any(required and not supported for required, supported in checks):
            return False
        return self.duration_s is None or spec.supports_duration(self.duration_s)

    def relaxed(self) -> Requirements | None:
        """Progressively drop soft constraints so a shot still gets made.

        Order matters: identity conditioning is the last thing we give up,
        because losing it is what breaks a drama.
        """
        for attr, floor in (
            ("needs_native_audio", False),
            ("needs_first_last_frame", False),
            ("min_quality", 0),
            ("duration_s", None),
            ("needs_reference_images", False),
        ):
            if getattr(self, attr) != floor:
                return replace(self, **{attr: floor})
        return None


@dataclass(slots=True)
class RouteDecision:
    role: str
    modality: Modality
    candidates: list[ModelSpec]
    relaxations: list[str] = field(default_factory=list)

    @property
    def primary(self) -> ModelSpec:
        return self.candidates[0]

    def describe(self) -> dict:
        return {
            "role": self.role,
            "modality": self.modality.value,
            "chain": [c.id for c in self.candidates],
            "relaxations": self.relaxations,
        }


class ModelRouter:
    def __init__(
        self,
        *,
        profile: RoutingProfile | str | None = None,
        ledger: CostLedger | None = None,
        health: HealthTracker | None = None,
        allowed_models: set[str] | None = None,
        blocked_models: set[str] | None = None,
        overrides: dict[str, str] | None = None,
        allow_simulation: bool = True,
        max_chain: int = 5,
    ):
        self.profile = profile if isinstance(profile, RoutingProfile) else get_profile(profile)
        self.ledger = ledger or CostLedger()
        self.health = health or global_health
        self.allowed_models = allowed_models
        self.blocked_models = blocked_models or set()
        self.overrides = overrides or {}     # role → forced model id
        self.allow_simulation = allow_simulation and self.profile.allow_simulation
        self.max_chain = max_chain
        self.decisions: list[RouteDecision] = []

    # ── selection ────────────────────────────────────────────────────────

    def _permitted(self, spec: ModelSpec) -> bool:
        if spec.id in self.blocked_models:
            return False
        if self.allowed_models is not None and spec.id not in self.allowed_models:
            return False
        if spec.provider == "simulation" and not self.allow_simulation:
            return False
        if spec.tier.rank > self.profile.max_tier.rank:
            return False
        return spec.available()

    def _score(self, spec: ModelSpec, role: str, position: int | None) -> float:
        """Lower is better."""
        score = 0.0
        if position is not None:                      # explicit profile preference
            score -= 1000 - position * 10
        score -= int(spec.quality) * 12 * self.profile.quality_weight
        score += spec.tier.rank * 4 * self.profile.cost_weight
        score += self.health.penalty(spec.id) * 60
        score += min(spec.typical_latency_s, 300) * 0.02
        if spec.provider == "simulation":
            score += 500                              # only ever a last resort
        return score

    def select(
        self,
        role: str,
        requirements: Requirements | None = None,
        *,
        modality: Modality | None = None,
    ) -> RouteDecision:
        modality = modality or ROLE_MODALITY.get(role, Modality.TEXT)
        reqs = requirements or Requirements()
        relaxations: list[str] = []

        forced = self.overrides.get(role)
        if forced:
            spec = CATALOG.get(forced)
            if spec and spec.available() and forced not in self.blocked_models:
                chain = [spec, *[
                    c for c in self._rank(role, modality, reqs) if c.id != forced
                ][: self.max_chain - 1]]
                decision = RouteDecision(role, modality, chain, ["forced_override"])
                self.decisions.append(decision)
                return decision
            log.warning("override_unavailable", extra={"role": role, "model_id": forced})

        current = reqs
        while True:
            ranked = self._rank(role, modality, current)
            if ranked:
                decision = RouteDecision(role, modality, ranked[: self.max_chain], relaxations)
                self.decisions.append(decision)
                log.debug("route_selected", extra=decision.describe())
                return decision
            nxt = current.relaxed()
            if nxt is None:
                break
            relaxations.append(_diff_requirements(current, nxt))
            current = nxt

        raise NoRouteError(
            f"no available model can serve role {role!r} ({modality.value}) under profile "
            f"{self.profile.name!r}",
            detail={
                "role": role,
                "modality": modality.value,
                "profile": self.profile.name,
                "hint": "add a provider API key, raise the routing profile, or enable simulation mode",
            },
        )

    def _rank(self, role: str, modality: Modality, reqs: Requirements) -> list[ModelSpec]:
        prefs = self.profile.preferred(role)
        pref_index = {mid: i for i, mid in enumerate(prefs)}
        pool = [
            s for s in list_specs(modality)
            if self._permitted(s) and reqs.matches(s)
        ]
        # A model whose breaker is open is demoted, not removed — if it is the
        # only candidate left we still want to try it.
        healthy = [s for s in pool if not self.health.is_open(s.id)]
        tripped = [s for s in pool if self.health.is_open(s.id)]
        ordered = sorted(healthy, key=lambda s: self._score(s, role, pref_index.get(s.id)))
        ordered += sorted(tripped, key=lambda s: self._score(s, role, pref_index.get(s.id)))
        return ordered

    # ── execution ────────────────────────────────────────────────────────

    async def _execute(self, role: str, decision: RouteDecision, call, estimate) -> ModelResponse:
        errors: list[str] = []
        chain: list[str] = []

        for spec in decision.candidates:
            est = estimate(spec)
            if not self.ledger.can_afford(est):
                errors.append(f"{spec.id}: skipped, ${est:.4f} exceeds remaining budget")
                continue

            chain.append(spec.id)
            provider = get_provider(spec.provider)
            sw = Stopwatch()
            try:
                response = await call(provider, spec)
            except ProviderError as exc:
                sw.stop()
                self.health.record_failure(spec.id, str(exc))
                errors.append(f"{spec.id}: {exc}")
                log.warning(
                    "model_call_failed",
                    extra={"role": role, "model_id": spec.id, "provider": spec.provider,
                           "retryable": exc.retryable, "error": str(exc)[:300]},
                )
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # defensive: a broken adapter must not kill a job
                sw.stop()
                self.health.record_failure(spec.id, repr(exc))
                errors.append(f"{spec.id}: unexpected {type(exc).__name__}: {exc}")
                log.exception("model_call_crashed", extra={"role": role, "model_id": spec.id})
                continue

            latency = sw.stop()
            response.latency_ms = response.latency_ms or latency
            response.fallback_chain = chain
            if not response.cost_usd:
                response.cost_usd = spec.estimate_cost(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_input_tokens=response.usage.cached_input_tokens,
                    seconds=response.usage.seconds,
                    images=response.usage.images,
                    characters=response.usage.characters,
                )
            self.health.record_success(spec.id, latency)
            self.ledger.record(LedgerEntry(
                role=role, model_id=spec.id, provider=spec.provider,
                cost_usd=response.cost_usd, latency_ms=latency,
                fallback_chain=chain.copy(),
            ))
            if len(chain) > 1:
                log.info("route_fallback_succeeded",
                         extra={"role": role, "model_id": spec.id, "tried": chain})
            return response

        if any("exceeds remaining budget" in e for e in errors) and len(errors) == len(decision.candidates):
            raise BudgetExceededError(
                f"role {role!r} could not run: every candidate exceeds the remaining budget",
                detail={"remaining_usd": self.ledger.remaining_usd, "tried": errors},
            )
        raise NoRouteError(
            f"all {len(decision.candidates)} candidates failed for role {role!r}",
            detail={"role": role, "attempts": errors},
        )

    # ── public API, one method per modality ──────────────────────────────

    async def text(self, role: str, req: LLMRequest, *, requirements: Requirements | None = None) -> ModelResponse:
        reqs = requirements or Requirements()
        if req.json_schema:
            reqs.needs_json_schema = True
        if req.image_urls:
            reqs.needs_vision = True
        modality = Modality.VISION if req.image_urls else ROLE_MODALITY.get(role, Modality.TEXT)
        decision = self.select(role, reqs, modality=modality)

        approx_in = len(req.system) // 4 + len(req.prompt) // 4 + 200
        approx_out = min(req.max_tokens, 4000)

        return await self._execute(
            role, decision,
            lambda provider, spec: provider.generate_text(spec, req),
            lambda spec: spec.estimate_cost(input_tokens=approx_in, output_tokens=approx_out),
        )

    async def image(self, role: str, req: ImageRequest, *, requirements: Requirements | None = None) -> ModelResponse:
        reqs = requirements or Requirements()
        if req.reference_image_urls or req.edit_source_url:
            reqs.needs_image_edit = True
            reqs.needs_reference_images = True
        decision = self.select(role, reqs, modality=Modality.IMAGE)
        return await self._execute(
            role, decision,
            lambda provider, spec: provider.generate_image(spec, req),
            lambda spec: spec.estimate_cost(images=req.n),
        )

    async def video(self, role: str, req: VideoRequest, *, requirements: Requirements | None = None) -> ModelResponse:
        reqs = requirements or Requirements()
        if req.first_frame_url:
            reqs.needs_image_to_video = True
        elif not reqs.needs_image_to_video:
            reqs.needs_text_to_video = True
        if req.last_frame_url:
            reqs.needs_first_last_frame = True
        if req.with_audio:
            reqs.needs_native_audio = True
        reqs.duration_s = req.duration_s
        decision = self.select(role, reqs, modality=Modality.VIDEO)
        return await self._execute(
            role, decision,
            lambda provider, spec: provider.generate_video(
                spec, _fit_video(req, spec)
            ),
            lambda spec: spec.estimate_cost(seconds=spec.clamp_duration(req.duration_s)),
        )

    async def speech(self, role: str, req: TTSRequest, *, requirements: Requirements | None = None) -> ModelResponse:
        decision = self.select(role, requirements or Requirements(), modality=Modality.TTS)
        return await self._execute(
            role, decision,
            lambda provider, spec: provider.synthesize_speech(spec, req),
            lambda spec: spec.estimate_cost(characters=len(req.text)),
        )

    async def audio(self, role: str, req: AudioRequest, *, requirements: Requirements | None = None) -> ModelResponse:
        modality = Modality.SFX if req.kind in ("sfx", "ambience") else Modality.MUSIC
        decision = self.select(role, requirements or Requirements(), modality=modality)
        return await self._execute(
            role, decision,
            lambda provider, spec: provider.generate_audio(
                spec, _fit_audio(req, spec)
            ),
            lambda spec: spec.estimate_cost(seconds=req.duration_s),
        )

    # ── introspection ────────────────────────────────────────────────────

    def plan(self, roles: list[str] | None = None) -> dict[str, list[str]]:
        """Dry-run the routing table — used by the /models/plan endpoint and docs."""
        out: dict[str, list[str]] = {}
        for role in roles or list(ROLE_MODALITY):
            try:
                out[role] = [c.id for c in self.select(role).candidates]
            except NexusError as exc:
                out[role] = [f"<unavailable: {exc.code}>"]
        return out


def _fit_video(req: VideoRequest, spec: ModelSpec) -> VideoRequest:
    """Clamp a request to what the chosen model actually accepts."""
    duration = spec.clamp_duration(req.duration_s)
    resolution = req.resolution
    if spec.resolutions and resolution not in spec.resolutions:
        resolution = spec.resolutions[-1]
    aspect = req.aspect_ratio
    if spec.aspect_ratios and aspect not in spec.aspect_ratios:
        aspect = spec.aspect_ratios[0]
    refs = req.reference_image_urls[: spec.max_reference_images] if spec.supports_reference_images else []
    return VideoRequest(
        prompt=req.prompt, negative_prompt=req.negative_prompt,
        first_frame_url=req.first_frame_url if spec.supports_image_to_video else None,
        last_frame_url=req.last_frame_url if spec.supports_first_last_frame else None,
        reference_image_urls=refs, duration_s=duration, aspect_ratio=aspect,
        resolution=resolution, fps=req.fps,
        with_audio=req.with_audio and spec.supports_native_audio,
        camera_hint=req.camera_hint, seed=req.seed, label=req.label,
    )


def _fit_audio(req: AudioRequest, spec: ModelSpec) -> AudioRequest:
    duration = spec.clamp_duration(req.duration_s) if spec.max_duration_s else req.duration_s
    return AudioRequest(prompt=req.prompt, duration_s=duration, kind=req.kind, label=req.label)


def _diff_requirements(before: Requirements, after: Requirements) -> str:
    changed = [
        name for name in before.__dataclass_fields__
        if getattr(before, name) != getattr(after, name)
    ]
    return f"relaxed {', '.join(changed)}" if changed else "relaxed"


def build_router(
    *,
    profile: str | None = None,
    budget_usd: float | None = None,
    allowed_models: set[str] | None = None,
    overrides: dict[str, str] | None = None,
    allow_simulation: bool = True,
) -> ModelRouter:
    return ModelRouter(
        profile=profile,
        ledger=CostLedger(budget_usd=budget_usd),
        allowed_models=allowed_models,
        overrides=overrides,
        allow_simulation=allow_simulation,
    )


__all__ = ["ModelRouter", "Requirements", "RouteDecision", "Tier", "build_router"]
