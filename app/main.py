from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import (
    APIError,
    PUBLIC_ERROR_RESPONSES,
    api_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.api.health import router as health_router
from app.api.generations import router as generations_router
from app.config import Settings, get_settings
from app.extension.gateway import router as extension_router
from app.runtime import build_runtime

MAX_REQUEST_BYTES = 70 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    runtime = build_runtime(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await runtime.bridge.close_background_tasks()
            runtime.projects.close()

    app = FastAPI(
        title="Flow Provider API",
        version="2.0.0",
        description="Google Flow API and orchestration service backed by live browser extensions.",
        responses=PUBLIC_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex}"
        try:
            try:
                content_length = request.headers.get("content-length")
                if content_length:
                    try:
                        body_length = int(content_length)
                    except ValueError as exc:
                        raise APIError(400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid.") from exc
                    if body_length > MAX_REQUEST_BYTES:
                        raise APIError(
                            413,
                            "PAYLOAD_TOO_LARGE",
                            "Request body exceeds the 70 MiB provider limit.",
                        )
                response = await call_next(request)
            except APIError as exc:
                response = await api_error_handler(request, exc)
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        finally:
            runtime = request.app.state.runtime
            for connection_id, credit_cost in getattr(request.state, "provider_reservations", []):
                runtime.release_connection(connection_id, credit_cost)

    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    for router in (health_router, generations_router, extension_router):
        app.include_router(router)
    return app


app = create_app()
