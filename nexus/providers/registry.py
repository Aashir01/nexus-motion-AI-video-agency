"""Lazy provider registry — adapters are constructed on first use."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from nexus.providers.base import Provider
from nexus.util.errors import ConfigurationError

_FACTORIES: dict[str, Callable[[], Provider]] = {}
_INSTANCES: dict[str, Provider] = {}


def register(provider_id: str, factory: Callable[[], Provider]) -> None:
    _FACTORIES[provider_id] = factory


def get_provider(provider_id: str) -> Provider:
    if provider_id in _INSTANCES:
        return _INSTANCES[provider_id]
    _bootstrap()
    factory = _FACTORIES.get(provider_id)
    if factory is None:
        raise ConfigurationError(f"no adapter registered for provider {provider_id!r}")
    _INSTANCES[provider_id] = factory()
    return _INSTANCES[provider_id]


async def close_all() -> None:
    await asyncio.gather(
        *(p.aclose() for p in _INSTANCES.values()), return_exceptions=True
    )
    _INSTANCES.clear()


_BOOTSTRAPPED = False


def _bootstrap() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True

    from nexus.providers import (  # noqa: F401  (import registers the adapters)
        anthropic_provider,
        edge_provider,
        elevenlabs_provider,
        fal_provider,
        google_provider,
        huggingface_provider,
        openai_compat,
        pollinations_provider,
        replicate_provider,
        runway_provider,
        simulation_provider,
    )
