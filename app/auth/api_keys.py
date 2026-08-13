from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select

from app.db.models import ApiClient
from app.ids import new_id

API_KEY_PREFIX = "fpa_live_"


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def key_prefix(value: str) -> str:
    return value[:12]


def generate_api_key() -> str:
    """Generate a client credential whose plaintext is safe to show once."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def ensure_bootstrap_client(session, api_key: str | None) -> None:
    if not api_key:
        return
    digest = hash_api_key(api_key)
    existing = session.scalar(select(ApiClient).where(ApiClient.key_hash == digest))
    if existing:return
    session.add(ApiClient(
        id=new_id("cli"), name="Bootstrap client", key_prefix=key_prefix(api_key), key_hash=digest,
        is_admin=False, priority=50, max_concurrent_jobs=10, rate_limit_per_minute=600,
    ))
    session.commit()


def authenticate_api_key(session, api_key: str) -> ApiClient | None:
    digest = hash_api_key(api_key)
    client = session.scalar(select(ApiClient).where(ApiClient.key_hash == digest, ApiClient.enabled.is_(True)))
    if client and hmac.compare_digest(client.key_hash, digest):
        return client
    return None
