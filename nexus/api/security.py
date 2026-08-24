"""Passwords, JWTs and API keys."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from nexus.config import settings
from nexus.util.errors import AuthError

_PBKDF2_ROUNDS = 210_000
API_KEY_PREFIX = "nmk"


# ── passwords ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


# ── JWT (HS256, no external dependency) ───────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(slots=True)
class TokenClaims:
    sub: str
    org_id: str
    role: str = "member"
    kind: str = "access"
    exp: int = 0
    jti: str = ""


def create_token(
    user_id: str, org_id: str, role: str = "member", *, kind: str = "access",
    ttl_seconds: int | None = None,
) -> str:
    ttl = ttl_seconds or (
        settings.access_token_ttl_minutes * 60 if kind == "access"
        else settings.refresh_token_ttl_days * 86400
    )
    payload = {
        "sub": user_id, "org_id": org_id, "role": role, "kind": kind,
        "iat": int(time.time()), "exp": int(time.time()) + ttl,
        "jti": secrets.token_urlsafe(8), "iss": settings.app_name,
    }
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.secret_key.encode(), f"{header}.{body}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{body}.{_b64(signature)}"


def decode_token(token: str, *, expected_kind: str = "access") -> TokenClaims:
    try:
        header, body, signature = token.split(".")
    except ValueError as exc:
        raise AuthError("malformed token") from exc

    expected = hmac.new(
        settings.secret_key.encode(), f"{header}.{body}".encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_unb64(signature), expected):
        raise AuthError("token signature is invalid")

    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed token payload") from exc

    if payload.get("exp", 0) < time.time():
        raise AuthError("token has expired")
    if payload.get("kind") != expected_kind:
        raise AuthError(f"expected a {expected_kind} token")

    return TokenClaims(
        sub=payload.get("sub", ""), org_id=payload.get("org_id", ""),
        role=payload.get("role", "member"), kind=payload.get("kind", "access"),
        exp=payload.get("exp", 0), jti=payload.get("jti", ""),
    )


# ── API keys ──────────────────────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, hash). The full key is shown exactly once."""
    body = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}_{body}"
    return full, full[: len(API_KEY_PREFIX) + 9], hash_api_key(full)


def hash_api_key(key: str) -> str:
    return hmac.new(settings.secret_key.encode(), key.encode(), hashlib.sha256).hexdigest()


def key_prefix(key: str) -> str:
    return key[: len(API_KEY_PREFIX) + 9]
