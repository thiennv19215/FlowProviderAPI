from __future__ import annotations

from collections.abc import Generator
import time

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import APIError
from app.auth.api_keys import authenticate_api_key

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Generator:
    session = request.app.state.runtime.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_client(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise APIError(401, "INVALID_API_KEY", "A valid Bearer API key is required.", headers={"WWW-Authenticate":"Bearer"})
    client = authenticate_api_key(db, credentials.credentials)
    if not client:
        raise APIError(401, "INVALID_API_KEY", "The supplied API key is invalid.", headers={"WWW-Authenticate":"Bearer"})
    allowed, remaining, reset = request.app.state.runtime.rate_limiter.hit(db,client.id,client.rate_limit_per_minute)
    request.state.rate_limit = (client.rate_limit_per_minute, remaining, reset)
    if not allowed:
        raise APIError(429, "RATE_LIMIT_EXCEEDED", "Too many API requests for this client.", retryable=True,headers={"Retry-After":str(max(1,reset-int(time.time())))})
    return client


def get_admin_client(client=Depends(get_client)):
    if not client.is_admin:
        raise APIError(403, "ADMIN_REQUIRED", "This endpoint is restricted to Provider administrators.")
    return client
