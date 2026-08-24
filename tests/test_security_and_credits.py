from __future__ import annotations

import pytest

from nexus.api.security import (
    create_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    key_prefix,
    verify_password,
)
from nexus.billing import credits
from nexus.billing.plans import estimate_episode_credits, get_plan, usd_to_credits
from nexus.db.models import Organization, PlanTier
from nexus.util.errors import AuthError, InsufficientCreditsError


def test_password_round_trip_and_rejection():
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong horse battery", stored)
    assert stored != hash_password("correct horse battery"), "salt is not random"


def test_short_passwords_are_refused():
    with pytest.raises(ValueError):
        hash_password("short")


def test_jwt_round_trip():
    token = create_token("usr_1", "org_1", "owner")
    claims = decode_token(token)
    assert (claims.sub, claims.org_id, claims.role) == ("usr_1", "org_1", "owner")


def test_tampered_jwt_is_rejected():
    token = create_token("usr_1", "org_1")
    header, _body, signature = token.split(".")
    forged = create_token("usr_2", "org_1").split(".")[1]
    with pytest.raises(AuthError):
        decode_token(f"{header}.{forged}.{signature}")


def test_expired_jwt_is_rejected():
    with pytest.raises(AuthError):
        decode_token(create_token("usr_1", "org_1", ttl_seconds=-10))


def test_refresh_token_cannot_be_used_as_access():
    token = create_token("usr_1", "org_1", kind="refresh")
    with pytest.raises(AuthError):
        decode_token(token, expected_kind="access")


def test_api_key_hashing_is_stable_and_prefix_matches():
    full, prefix, digest = generate_api_key()
    assert full.startswith("nmk_")
    assert prefix == key_prefix(full)
    assert digest == hash_api_key(full)


def test_credit_conversion_never_undercharges():
    assert usd_to_credits(0.0001) >= 1
    assert usd_to_credits(0) == 0
    assert usd_to_credits(1.0) > 100  # markup is applied


def test_plan_estimates_scale_with_runtime_and_quality():
    cheap = estimate_episode_credits(720, "budget")
    rich = estimate_episode_credits(720, "flagship")
    assert rich > cheap * 3
    assert estimate_episode_credits(1440, "balanced") > estimate_episode_credits(720, "balanced")


def test_free_plan_cannot_select_premium_models():
    assert not get_plan(PlanTier.FREE).allows_profile("flagship")
    assert get_plan(PlanTier.SCALE).allows_profile("flagship")


async def test_reserve_settle_refunds_the_unused_portion(db):
    org = Organization(name="T", slug="t-settle", plan=PlanTier.STUDIO, credit_balance=10_000)
    db.add(org)
    await db.flush()

    await credits.reserve(db, org.id, 5_000, "job_1")
    assert await credits.balance(db, org.id) == 5_000

    charged, balance = await credits.settle(db, org.id, "job_1", reserved=5_000, actual_usd=1.0)
    assert charged == usd_to_credits(1.0)
    assert balance == 10_000 - charged


async def test_failed_job_costs_nothing(db):
    org = Organization(name="T", slug="t-refund", plan=PlanTier.STUDIO, credit_balance=8_000)
    db.add(org)
    await db.flush()

    await credits.reserve(db, org.id, 3_000, "job_2")
    await credits.refund_reservation(db, org.id, "job_2", 3_000)
    assert await credits.balance(db, org.id) == 8_000


async def test_reservation_beyond_balance_is_refused(db):
    org = Organization(name="T", slug="t-poor", plan=PlanTier.FREE, credit_balance=100)
    db.add(org)
    await db.flush()

    with pytest.raises(InsufficientCreditsError):
        await credits.reserve(db, org.id, 5_000, "job_3")
    assert await credits.balance(db, org.id) == 100


async def test_balance_is_reconstructible_from_the_ledger(db):
    org = Organization(name="T", slug="t-ledger", plan=PlanTier.STUDIO, credit_balance=0)
    db.add(org)
    await db.flush()

    await credits.purchase(db, org.id, 5_000)
    await credits.reserve(db, org.id, 2_000, "job_4")
    await credits.settle(db, org.id, "job_4", reserved=2_000, actual_usd=5.0)

    entries = await credits.history(db, org.id, limit=100)
    assert sum(e.delta for e in entries) == await credits.balance(db, org.id)
