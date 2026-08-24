"""Prefixed, sortable identifiers (Stripe-style) for every entity."""
from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford-ish, no ambiguity


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[n % 32])
        n //= 32
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """Time-ordered id: prefix_<48-bit ms><40-bit random>."""
    ts = _b32(int(time.time() * 1000), 10)
    rand = _b32(secrets.randbits(40), 8)
    return f"{prefix}_{ts}{rand}"


def short_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)
