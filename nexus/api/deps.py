"""Shared FastAPI dependencies: identity, tenancy and plan enforcement."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.api.security import decode_token, hash_api_key, key_prefix
from nexus.billing.plans import Plan, get_plan
from nexus.db.base import utcnow
from nexus.db.models import ApiKey, Organization, User, UserRole
from nexus.db.session import get_session
from nexus.util.errors import AuthError, ForbiddenError, NotFoundError


@dataclass(slots=True)
class Principal:
    """Who is calling and what they are allowed to do."""

    org: Organization
    user: User | None = None
    api_key: ApiKey | None = None

    @property
    def org_id(self) -> str:
        return self.org.id

    @property
    def plan(self) -> Plan:
        return get_plan(self.org.plan)

    @property
    def role(self) -> UserRole:
        return self.user.role if self.user else UserRole.MEMBER

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.OWNER, UserRole.ADMIN) or bool(
            self.user and self.user.is_superuser
        )

    def require_admin(self) -> None:
        if not self.is_admin:
            raise ForbiddenError("this action requires an owner or admin role")


async def get_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """Accepts either a bearer JWT (dashboard) or an API key (programmatic)."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if x_api_key or (token and token.startswith("nmk_")):
        raw = x_api_key or token or ""
        record = (await session.execute(
            select(ApiKey).where(
                ApiKey.prefix == key_prefix(raw), ApiKey.key_hash == hash_api_key(raw)
            )
        )).scalar_one_or_none()
        if record is None or record.revoked_at is not None:
            raise AuthError("invalid or revoked API key")
        org = await session.get(Organization, record.org_id)
        if org is None:
            raise AuthError("the organisation for this key no longer exists")
        if not get_plan(org.plan).api_access:
            raise ForbiddenError("API access requires a paid plan")
        record.last_used_at = utcnow()
        await session.commit()
        request.state.org_id = org.id
        return Principal(org=org, api_key=record)

    if not token:
        raise AuthError("missing credentials: send a bearer token or an X-API-Key header")

    claims = decode_token(token)
    user = await session.get(User, claims.sub)
    if user is None or not user.is_active:
        raise AuthError("this account is no longer active")
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise AuthError("the organisation for this account no longer exists")
    request.state.org_id = org.id
    return Principal(org=org, user=user)


async def get_admin(principal: Principal = Depends(get_principal)) -> Principal:
    principal.require_admin()
    return principal


async def get_superuser(principal: Principal = Depends(get_principal)) -> Principal:
    if not (principal.user and principal.user.is_superuser):
        raise ForbiddenError("staff only")
    return principal


async def owned_or_404(session: AsyncSession, model, entity_id: str, org_id: str):
    """Every tenant-scoped fetch goes through here — a missing row and someone
    else's row are indistinguishable from the outside, which is the point."""
    entity = await session.get(model, entity_id)
    if entity is None or getattr(entity, "org_id", None) != org_id:
        raise NotFoundError(f"{model.__name__.lower()} {entity_id} not found")
    return entity
