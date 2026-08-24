"""Credit ledger with reservations.

A production is quoted before it starts and settled when it ends.  The
reservation is what stops a customer from launching ten flagship episodes on a
balance that covers one; settlement refunds the difference, so nobody pays for
a job that fell back to cheaper models or died at shot 3.

Every mutation goes through `_apply`, which locks the organisation row, writes
an immutable ledger entry, and updates the cached balance in the same
transaction — the balance is therefore always reconstructible from the ledger.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.billing.plans import get_plan, usd_to_credits
from nexus.config import settings
from nexus.db.models import CreditEntry, Organization
from nexus.util.errors import InsufficientCreditsError, NotFoundError
from nexus.util.logging import get_logger

log = get_logger(__name__)

REASON_GRANT = "grant"
REASON_PURCHASE = "purchase"
REASON_RESERVE = "reserve"
REASON_SETTLE = "settle"
REASON_REFUND = "refund"
REASON_ADJUST = "adjustment"


async def _load_for_update(session: AsyncSession, org_id: str) -> Organization:
    stmt = select(Organization).where(Organization.id == org_id)
    if session.bind and session.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is None:
        raise NotFoundError(f"organization {org_id} not found")
    return org


async def _apply(
    session: AsyncSession, org_id: str, delta: int, reason: str,
    *, job_id: str | None = None, note: str = "", external_ref: str | None = None,
    allow_negative: bool = False,
) -> int:
    org = await _load_for_update(session, org_id)
    new_balance = org.credit_balance + delta
    if new_balance < 0 and not allow_negative:
        raise InsufficientCreditsError(
            f"this needs {abs(delta)} credits but the balance is {org.credit_balance}",
            detail={"required": abs(delta), "balance": org.credit_balance,
                    "shortfall": abs(new_balance)},
        )
    org.credit_balance = new_balance
    session.add(CreditEntry(
        org_id=org_id, delta=delta, balance_after=new_balance, reason=reason,
        job_id=job_id, note=note[:1000], external_ref=external_ref,
    ))
    await session.flush()
    log.info("credits_applied", extra={"org_id": org_id, "delta": delta, "reason": reason,
                                       "balance": new_balance, "job_id": job_id})
    return new_balance


async def balance(session: AsyncSession, org_id: str) -> int:
    org = (await session.execute(
        select(Organization).where(Organization.id == org_id)
    )).scalar_one_or_none()
    if org is None:
        raise NotFoundError(f"organization {org_id} not found")
    return org.credit_balance


async def grant_monthly(session: AsyncSession, org_id: str, *, external_ref: str | None = None) -> int:
    """Top the account up to its plan allowance at the start of a billing period."""
    org = await _load_for_update(session, org_id)
    plan = get_plan(org.plan)
    if plan.monthly_credits <= 0:
        return org.credit_balance
    return await _apply(
        session, org_id, plan.monthly_credits, REASON_GRANT,
        note=f"{plan.name} monthly allowance", external_ref=external_ref,
    )


async def purchase(session: AsyncSession, org_id: str, credits: int, *,
                   external_ref: str | None = None, note: str = "") -> int:
    return await _apply(session, org_id, credits, REASON_PURCHASE,
                        note=note or "credit purchase", external_ref=external_ref)


async def adjust(session: AsyncSession, org_id: str, credits: int, note: str = "") -> int:
    return await _apply(session, org_id, credits, REASON_ADJUST, note=note, allow_negative=True)


async def reserve(session: AsyncSession, org_id: str, credits: int, job_id: str) -> int:
    """Hold the quoted cost up front. Raises if the balance can't cover it."""
    if credits <= 0:
        return await balance(session, org_id)
    if not settings.billing_enabled:
        log.debug("billing_disabled_reservation_skipped", extra={"org_id": org_id})
        return await balance(session, org_id)
    return await _apply(session, org_id, -credits, REASON_RESERVE, job_id=job_id,
                        note=f"reserved for job {job_id}")


async def settle(
    session: AsyncSession, org_id: str, job_id: str, *, reserved: int, actual_usd: float
) -> tuple[int, int]:
    """Convert a reservation into a real charge.

    Returns (credits_charged, balance). Over-spend beyond the reservation is
    charged too — but the reservation is deliberately generous, and the router's
    budget guard stops a job before it can run far past its quote.
    """
    charged = usd_to_credits(actual_usd)
    if not settings.billing_enabled:
        return charged, await balance(session, org_id)

    difference = reserved - charged
    if difference > 0:
        new_balance = await _apply(
            session, org_id, difference, REASON_REFUND, job_id=job_id,
            note=f"unused reservation returned ({difference} credits)",
        )
    elif difference < 0:
        new_balance = await _apply(
            session, org_id, difference, REASON_SETTLE, job_id=job_id,
            note=f"overage beyond reservation ({abs(difference)} credits)",
            allow_negative=True,
        )
    else:
        new_balance = await balance(session, org_id)
    return charged, new_balance


async def refund_reservation(session: AsyncSession, org_id: str, job_id: str, reserved: int) -> int:
    """A job that never produced anything costs nothing."""
    if reserved <= 0 or not settings.billing_enabled:
        return await balance(session, org_id)
    return await _apply(session, org_id, reserved, REASON_REFUND, job_id=job_id,
                        note="job failed — reservation returned in full")


async def history(session: AsyncSession, org_id: str, limit: int = 50) -> list[CreditEntry]:
    rows = await session.execute(
        select(CreditEntry).where(CreditEntry.org_id == org_id)
        .order_by(CreditEntry.created_at.desc()).limit(limit)
    )
    return list(rows.scalars())
