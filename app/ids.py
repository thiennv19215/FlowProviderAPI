from __future__ import annotations
import secrets
import uuid

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_compact_id(prefix: str) -> str:
    """Return a URL-safe public ID with 96 bits of randomness."""
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def new_numeric_id() -> str:
    """Return a 15-digit opaque public media identifier as a string."""
    return str(secrets.randbelow(900_000_000_000_000) + 100_000_000_000_000)
