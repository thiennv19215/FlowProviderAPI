from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import (
    APIError,
    PUBLIC_ERROR_RESPONSES,
    api_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.api.characters import router as characters_router
from app.api.health import router as health_router
from app.api.generations import router as generations_router
from app.config import Settings, get_settings
from app.extension.gateway import router as extension_router
from app.runtime import build_runtime

MAX_REQUEST_BYTES = 70 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class RequestGuardMiddleware:
    """Enforce the actual streamed body size for public business calls."""

    def __init__(self, app, *, settings: Settings, max_request_bytes: int):
        self.app = app
        self.settings = settings
        self.max_request_bytes = max_request_bytes

    @staticmethod
    def _headers(scope) -> dict[bytes, bytes]:
        return {key.lower(): value for key, value in scope.get("headers") or []}

    async def _reject(self, scope, receive, send, status_code: int, code: str, message: str):
        headers = self._headers(scope)
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or f"req_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id
        response = JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "status_code": status_code,
                    "code": code,
                    "message": message,
                    "details": [],
                    "request_id": request_id,
                    "retryable": False,
                }
            },
            headers={"X-Request-Id": request_id},
        )
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = self._headers(scope)
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or f"req_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._reject(
                    scope, receive, send, 400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid."
                )
                return
            if declared_length < 0:
                await self._reject(
                    scope, receive, send, 400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid."
                )
                return
            if declared_length > self.max_request_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    413,
                    "PAYLOAD_TOO_LARGE",
                    "Request body exceeds the 70 MiB provider limit.",
                )
                return

        buffered = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                await self.app(scope, lambda: message, send)
                return
            buffered.extend(message.get("body") or b"")
            if len(buffered) > self.max_request_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    413,
                    "PAYLOAD_TOO_LARGE",
                    "Request body exceeds the 70 MiB provider limit.",
                )
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(buffered), "more_body": False}

        await self.app(scope, replay_receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    runtime = build_runtime(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if runtime.worker:
            await runtime.worker.start()
        try:
            yield
        finally:
            if runtime.worker:
                await runtime.worker.stop()
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
        request.state.request_id = (
            getattr(request.state, "request_id", None)
            or request.headers.get("X-Request-Id")
            or f"req_{uuid.uuid4().hex}"
        )
        try:
            try:
                response = await call_next(request)
            except APIError as exc:
                response = await api_error_handler(request, exc)
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        finally:
            runtime = request.app.state.runtime
            for connection_id, credit_cost in getattr(request.state, "provider_reservations", []):
                runtime.release_connection(connection_id, credit_cost)

    app.add_middleware(
        RequestGuardMiddleware,
        settings=settings,
        max_request_bytes=MAX_REQUEST_BYTES,
    )
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    for router in (health_router, generations_router, characters_router, extension_router):
        app.include_router(router)
    return app


app = create_app()
