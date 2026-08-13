from __future__ import annotations

import logging
import uuid

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Flow Provider API",
        version="2.0.0",
        description="Google Flow API facade backed by a live browser extension.",
        responses=PUBLIC_ERROR_RESPONSES,
    )
    app.state.runtime = build_runtime(settings)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex}"
        try:
            response = await call_next(request)
        except APIError as exc:
            response = await api_error_handler(request, exc)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    for router in (health_router, generations_router, extension_router):
        app.include_router(router)
    return app


app = create_app()
