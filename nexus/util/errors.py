"""Error taxonomy shared by the router, pipeline and API layers."""
from __future__ import annotations


class NexusError(Exception):
    """Base class. `code` is stable and safe to expose to clients."""

    code = "nexus_error"
    http_status = 500
    retryable = False

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **({"detail": self.detail} if self.detail else {})}}


class ConfigurationError(NexusError):
    code = "configuration_error"
    http_status = 500


class AuthError(NexusError):
    code = "unauthorized"
    http_status = 401


class ForbiddenError(NexusError):
    code = "forbidden"
    http_status = 403


class NotFoundError(NexusError):
    code = "not_found"
    http_status = 404


class ValidationError(NexusError):
    code = "validation_error"
    http_status = 422


class ConflictError(NexusError):
    code = "conflict"
    http_status = 409


class RateLimitedError(NexusError):
    code = "rate_limited"
    http_status = 429
    retryable = True


class InsufficientCreditsError(NexusError):
    code = "insufficient_credits"
    http_status = 402


class BudgetExceededError(NexusError):
    code = "budget_exceeded"
    http_status = 402


class ProviderError(NexusError):
    """A provider call failed. `retryable` drives router fallback behaviour."""

    code = "provider_error"
    http_status = 502
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        model: str | None = None,
        status: int | None = None,
        retryable: bool = True,
        detail: dict | None = None,
    ):
        super().__init__(message, detail=detail)
        self.provider = provider
        self.model = model
        self.status = status
        self.retryable = retryable

    def __str__(self) -> str:
        bits = [self.provider]
        if self.model:
            bits.append(self.model)
        if self.status:
            bits.append(f"HTTP {self.status}")
        return f"[{' · '.join(bits)}] {self.message}"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class NoRouteError(NexusError):
    """No model in the catalog satisfies the request under current constraints."""

    code = "no_route_available"
    http_status = 503


class RenderError(NexusError):
    code = "render_error"
    http_status = 500


class PipelineError(NexusError):
    code = "pipeline_error"
    http_status = 500
