from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.api.deps import Principal, get_admin, get_principal
from nexus.api.schemas import (
    ApiKeyCreate,
    ApiKeyOut,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from nexus.api.security import (
    create_token,
    decode_token,
    generate_api_key,
    hash_password,
    verify_password,
)
from nexus.billing import credits
from nexus.config import settings
from nexus.db.base import utcnow
from nexus.db.models import ApiKey, Organization, PlanTier, User, UserRole
from nexus.db.session import get_session
from nexus.util.errors import AuthError, ConflictError, NotFoundError

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "studio"


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(payload: SignupRequest, session: AsyncSession = Depends(get_session)):
    existing = (await session.execute(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )).scalar_one_or_none()
    if existing:
        raise ConflictError("an account with this email already exists")

    base_slug = _slugify(payload.organization_name or payload.email.split("@")[0])
    slug = base_slug
    suffix = 1
    while (await session.execute(
        select(Organization).where(Organization.slug == slug)
    )).scalar_one_or_none():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    org = Organization(
        name=payload.organization_name or f"{payload.email.split('@')[0]}'s studio",
        slug=slug, plan=PlanTier.FREE,
    )
    session.add(org)
    await session.flush()

    user = User(
        org_id=org.id, email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.email.split("@")[0],
        role=UserRole.OWNER,
    )
    session.add(user)
    await session.flush()

    # Every new studio gets its free monthly allowance immediately.
    await credits.grant_monthly(session, org.id, external_ref=f"signup:{org.id}")
    await session.commit()

    return _tokens(user, org)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )).scalar_one_or_none()
    # Same error either way — do not leak which emails are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthError("email or password is incorrect")
    if not user.is_active:
        raise AuthError("this account has been deactivated")

    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFoundError("organisation not found")
    user.last_login_at = utcnow()
    await session.commit()
    return _tokens(user, org)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, session: AsyncSession = Depends(get_session)):
    claims = decode_token(payload.refresh_token, expected_kind="refresh")
    user = await session.get(User, claims.sub)
    if user is None or not user.is_active:
        raise AuthError("this account is no longer active")
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFoundError("organisation not found")
    return _tokens(user, org)


@router.get("/me", response_model=UserOut)
async def me(principal: Principal = Depends(get_principal)):
    user = principal.user
    return UserOut(
        id=user.id if user else "api-key",
        email=user.email if user else f"{principal.api_key.name}@apikey",
        display_name=user.display_name if user else principal.api_key.name,
        role=principal.role.value, org_id=principal.org_id,
        org_name=principal.org.name, plan=principal.org.plan.value,
        credit_balance=principal.org.credit_balance,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(principal: Principal = Depends(get_admin),
                    session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(ApiKey).where(ApiKey.org_id == principal.org_id).order_by(ApiKey.created_at.desc())
    )).scalars()
    return [
        ApiKeyOut(id=k.id, name=k.name, prefix=k.prefix, created_at=k.created_at,
                  last_used_at=k.last_used_at, revoked=k.revoked_at is not None)
        for k in rows
    ]


@router.post("/api-keys", response_model=ApiKeyOut, status_code=201)
async def create_key(payload: ApiKeyCreate, principal: Principal = Depends(get_admin),
                     session: AsyncSession = Depends(get_session)):
    secret, prefix, key_hash = generate_api_key()
    record = ApiKey(
        org_id=principal.org_id, name=payload.name, prefix=prefix, key_hash=key_hash,
        created_by=principal.user.id if principal.user else None,
    )
    session.add(record)
    await session.commit()
    return ApiKeyOut(
        id=record.id, name=record.name, prefix=record.prefix,
        created_at=record.created_at, secret=secret,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, principal: Principal = Depends(get_admin),
                     session: AsyncSession = Depends(get_session)):
    record = await session.get(ApiKey, key_id)
    if record is None or record.org_id != principal.org_id:
        raise NotFoundError(f"api key {key_id} not found")
    record.revoked_at = utcnow()
    await session.commit()


def _tokens(user: User, org: Organization) -> TokenResponse:
    return TokenResponse(
        access_token=create_token(user.id, org.id, user.role.value),
        refresh_token=create_token(user.id, org.id, user.role.value, kind="refresh"),
        expires_in=settings.access_token_ttl_minutes * 60,
    )
