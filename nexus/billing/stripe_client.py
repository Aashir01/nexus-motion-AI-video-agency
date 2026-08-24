"""Stripe subscriptions and credit top-ups.

Kept deliberately thin: checkout sessions in, webhooks out.  Everything that
matters to the product (plan tier, credit balance) lives in our own tables, so
Stripe being unreachable degrades billing, not the product.
"""
from __future__ import annotations

from typing import Any

from nexus.billing.plans import (
    CREDIT_USD,
    get_plan,
    stripe_price_id,
    tier_for_price_id,
)
from nexus.config import settings
from nexus.db.models import Organization, PlanTier
from nexus.util.errors import ConfigurationError, ProviderError
from nexus.util.logging import get_logger

log = get_logger(__name__)


def _client():
    if not settings.stripe_secret_key:
        raise ConfigurationError("STRIPE_SECRET_KEY is not configured")
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover
        raise ConfigurationError("the `stripe` package is required for billing") from exc
    stripe.api_key = settings.stripe_secret_key
    return stripe


def billing_available() -> bool:
    return bool(settings.billing_enabled and settings.stripe_secret_key)


async def ensure_customer(org: Organization, email: str) -> str:
    if org.stripe_customer_id:
        return org.stripe_customer_id
    stripe = _client()
    customer = stripe.Customer.create(
        email=email, name=org.name, metadata={"org_id": org.id, "slug": org.slug}
    )
    org.stripe_customer_id = customer.id
    return customer.id


async def create_subscription_checkout(org: Organization, email: str, tier: PlanTier) -> str:
    price = stripe_price_id(tier)
    if not price:
        raise ConfigurationError(f"no Stripe price configured for the {tier.value} plan")
    stripe = _client()
    customer_id = await ensure_customer(org, email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price, "quantity": 1}],
        success_url=f"{settings.frontend_base_url}/billing?status=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.frontend_base_url}/billing?status=cancelled",
        allow_promotion_codes=True,
        subscription_data={"metadata": {"org_id": org.id, "tier": tier.value}},
        metadata={"org_id": org.id, "tier": tier.value},
    )
    return session.url


async def create_credit_checkout(org: Organization, email: str, credits: int) -> str:
    """One-off top-up for customers who burn through their monthly allowance."""
    if credits < 1000:
        raise ProviderError("minimum top-up is 1,000 credits", provider="stripe", retryable=False)
    stripe = _client()
    customer_id = await ensure_customer(org, email)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": round(credits * CREDIT_USD * 100),
                "product_data": {
                    "name": f"{credits:,} Nexus Motion credits",
                    "description": "Production credits for episode generation",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{settings.frontend_base_url}/billing?status=topped_up",
        cancel_url=f"{settings.frontend_base_url}/billing?status=cancelled",
        metadata={"org_id": org.id, "credits": str(credits), "kind": "credit_topup"},
    )
    return session.url


async def create_portal_session(org: Organization) -> str:
    if not org.stripe_customer_id:
        raise ProviderError("this organisation has no Stripe customer yet",
                            provider="stripe", retryable=False)
    stripe = _client()
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id, return_url=f"{settings.frontend_base_url}/billing"
    )
    return session.url


def verify_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    if not settings.stripe_webhook_secret:
        raise ConfigurationError("STRIPE_WEBHOOK_SECRET is not configured")
    stripe = _client()
    try:
        return stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except Exception as exc:
        raise ProviderError(f"webhook signature verification failed: {exc}",
                            provider="stripe", retryable=False) from exc


def tier_from_subscription(subscription: dict) -> PlanTier | None:
    items = (subscription.get("items") or {}).get("data") or []
    for item in items:
        price_id = (item.get("price") or {}).get("id")
        if price_id:
            tier = tier_for_price_id(price_id)
            if tier:
                return tier
    metadata_tier = (subscription.get("metadata") or {}).get("tier")
    if metadata_tier:
        try:
            return PlanTier(metadata_tier)
        except ValueError:
            return None
    return None


def public_plans() -> list[dict]:
    return [get_plan(t).to_dict() for t in
            (PlanTier.FREE, PlanTier.STARTER, PlanTier.STUDIO, PlanTier.SCALE, PlanTier.ENTERPRISE)]
