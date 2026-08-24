from __future__ import annotations

from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.api.deps import Principal, get_admin, get_principal
from nexus.api.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    CreditEntryOut,
    TopUpRequest,
    UsageSummary,
)
from nexus.billing import credits, stripe_client
from nexus.billing.plans import get_plan
from nexus.db.base import utcnow
from nexus.db.models import Organization, PlanTier, UsageRecord, WebhookEvent
from nexus.db.session import get_session, session_scope
from nexus.util.errors import ConfigurationError
from nexus.util.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans")
async def plans():
    """Public pricing — no auth required."""
    return {"plans": stripe_client.public_plans(),
            "billing_enabled": stripe_client.billing_available()}


@router.get("/account")
async def account(principal: Principal = Depends(get_principal)):
    org = principal.org
    plan = get_plan(org.plan)
    return {
        "organization": {"id": org.id, "name": org.name, "slug": org.slug},
        "plan": plan.to_dict(),
        "credit_balance": org.credit_balance,
        "credit_balance_usd": round(org.credit_balance * 0.01, 2),
        "subscription_status": org.subscription_status,
        "current_period_end": org.current_period_end,
        "billing_enabled": stripe_client.billing_available(),
    }


@router.get("/credits", response_model=list[CreditEntryOut])
async def credit_history(principal: Principal = Depends(get_principal),
                         session: AsyncSession = Depends(get_session), limit: int = 50):
    entries = await credits.history(session, principal.org_id, limit)
    return [
        CreditEntryOut(id=e.id, delta=e.delta, balance_after=e.balance_after, reason=e.reason,
                       note=e.note, job_id=e.job_id, created_at=e.created_at)
        for e in entries
    ]


@router.get("/usage", response_model=UsageSummary)
async def usage(principal: Principal = Depends(get_principal),
                session: AsyncSession = Depends(get_session), days: int = 30):
    since = utcnow() - timedelta(days=days)
    rows = (await session.execute(
        select(UsageRecord).where(
            UsageRecord.org_id == principal.org_id, UsageRecord.created_at >= since
        )
    )).scalars().all()

    by_model: dict[str, float] = {}
    by_role: dict[str, float] = {}
    for record in rows:
        by_model[record.model_id] = round(by_model.get(record.model_id, 0.0) + record.cost_usd, 6)
        by_role[record.role] = round(by_role.get(record.role, 0.0) + record.cost_usd, 6)

    return UsageSummary(
        period_days=days,
        total_cost_usd=round(sum(r.cost_usd for r in rows), 4),
        total_calls=len(rows),
        by_model=dict(sorted(by_model.items(), key=lambda kv: -kv[1])[:25]),
        by_role=dict(sorted(by_role.items(), key=lambda kv: -kv[1])),
        fallback_rate=round(sum(1 for r in rows if r.used_fallback) / len(rows), 4) if rows else 0.0,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(payload: CheckoutRequest, principal: Principal = Depends(get_admin),
                   session: AsyncSession = Depends(get_session)):
    if not stripe_client.billing_available():
        raise ConfigurationError("billing is not enabled on this deployment")
    email = principal.user.email if principal.user else f"billing@{principal.org.slug}"
    url = await stripe_client.create_subscription_checkout(
        principal.org, email, PlanTier(payload.tier)
    )
    await session.commit()
    return CheckoutResponse(checkout_url=url)


@router.post("/top-up", response_model=CheckoutResponse)
async def top_up(payload: TopUpRequest, principal: Principal = Depends(get_admin),
                 session: AsyncSession = Depends(get_session)):
    if not stripe_client.billing_available():
        raise ConfigurationError("billing is not enabled on this deployment")
    email = principal.user.email if principal.user else f"billing@{principal.org.slug}"
    url = await stripe_client.create_credit_checkout(principal.org, email, payload.credits)
    await session.commit()
    return CheckoutResponse(checkout_url=url)


@router.post("/portal", response_model=CheckoutResponse)
async def portal(principal: Principal = Depends(get_admin)):
    if not stripe_client.billing_available():
        raise ConfigurationError("billing is not enabled on this deployment")
    return CheckoutResponse(checkout_url=await stripe_client.create_portal_session(principal.org))


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature")):
    """Stripe delivers at-least-once; `webhook_events` makes handling idempotent."""
    payload = await request.body()
    event = stripe_client.verify_webhook(payload, stripe_signature)
    event_id = event.get("id", "")
    event_type = event.get("type", "")

    async with session_scope() as session:
        if await session.get(WebhookEvent, event_id):
            return {"status": "already_processed"}
        session.add(WebhookEvent(id=event_id, event_type=event_type, processed_at=utcnow()))
        await _handle_event(session, event_type, event.get("data", {}).get("object", {}))

    log.info("stripe_webhook", extra={"event_type": event_type, "event_id": event_id})
    return {"status": "ok"}


async def _handle_event(session: AsyncSession, event_type: str, obj: dict) -> None:
    if event_type == "checkout.session.completed" and (obj.get("metadata") or {}).get("kind") == "credit_topup":
        metadata = obj["metadata"]
        org_id = metadata.get("org_id")
        amount = int(metadata.get("credits", 0))
        if org_id and amount:
            await credits.purchase(session, org_id, amount,
                                   external_ref=obj.get("id"), note="Stripe top-up")
        return

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        org = await _org_for(session, obj)
        if not org:
            return
        tier = stripe_client.tier_from_subscription(obj)
        if tier:
            upgraded = tier != org.plan
            org.plan = tier
            if upgraded:
                # Immediate access to the new allowance is what people expect
                # after paying; the next period grant handles the rest.
                await credits.grant_monthly(session, org.id, external_ref=obj.get("id"))
        org.stripe_subscription_id = obj.get("id")
        org.subscription_status = obj.get("status")
        period_end = obj.get("current_period_end")
        if period_end:
            from datetime import datetime

            org.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)
        return

    if event_type == "customer.subscription.deleted":
        org = await _org_for(session, obj)
        if org:
            org.plan = PlanTier.FREE
            org.subscription_status = "canceled"
            org.stripe_subscription_id = None
        return

    if event_type == "invoice.paid":
        org = await _org_for(session, obj)
        if org and (obj.get("billing_reason") == "subscription_cycle"):
            await credits.grant_monthly(session, org.id, external_ref=obj.get("id"))


async def _org_for(session: AsyncSession, obj: dict) -> Organization | None:
    org_id = (obj.get("metadata") or {}).get("org_id")
    if org_id:
        return await session.get(Organization, org_id)
    customer = obj.get("customer")
    if not customer:
        return None
    return (await session.execute(
        select(Organization).where(Organization.stripe_customer_id == customer)
    )).scalar_one_or_none()
