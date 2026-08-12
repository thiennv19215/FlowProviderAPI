from __future__ import annotations
import secrets
import uuid

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_compact_id(prefix: str) -> str:
    """Return a URL-safe public ID with 96 bits of randomness."""
    return f"{prefix}_{secrets.token_urlsafe(12)}"
