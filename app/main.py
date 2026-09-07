from __future__ import annotations

import hmac
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
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
    """Authenticate business calls, limit request size, and apply queue backpressure."""

    def __init__(self, app, *, settings: Settings, max_request_bytes: int):
        self.app = app
        self.settings = settings
        self.max_request_bytes = max_request_bytes

    @staticmethod
    def _headers(scope) -> dict[bytes, bytes]:
        return {key.lower(): value for key, value in scope.get("headers") or []}

    @staticmethod
    def _is_generation_create(scope) -> bool:
        if str(scope.get("method") or "").upper() != "POST":
            return False
        path = str(scope.get("path") or "")
        if path in {"/v1/images/generations", "/v1/videos/generations"}:
            return True
        return (
            path.startswith("/v1/characters/")
            and path.endswith(("/images/generations", "/videos/generations"))
        )

    async def _reject(
        self,
        scope,
        receive,
        send,
        status_code: int,
        code: str,
        message: str,
        *,
        response_headers: dict[str, str] | None = None,
        retryable: bool = False,
    ):
        headers = self._headers(scope)
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or f"req_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id
        output_headers = {"X-Request-Id": request_id}
        output_headers.update(response_headers or {})
        response = JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "status_code": status_code,
                    "code": code,
                    "message": message,
                    "details": [],
                    "request_id": request_id,
                    "retryable": retryable,
                }
            },
            headers=output_headers,
        )
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = self._headers(scope)
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or f"req_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id

        path = str(scope.get("path") or "")
        is_business_path = path == "/v1" or path.startswith("/v1/")
        if self.settings.env == "production" and is_business_path:
            expected_key = self.settings.bootstrap_api_key or ""
            raw_authorization = headers.get(b"authorization", b"").decode("latin-1").strip()
            scheme, separator, supplied_key = raw_authorization.partition(" ")
            supplied_key = supplied_key.strip()
            valid = (
                separator == " "
                and scheme.lower() == "bearer"
                and bool(supplied_key)
                and bool(expected_key)
                and hmac.compare_digest(expected_key, supplied_key)
            )
            if not valid:
                await self._reject(
                    scope,
                    receive,
                    send,
                    401,
                    "INVALID_API_KEY",
                    "A valid Bearer API key is required.",
                    response_headers={"WWW-Authenticate": "Bearer"},
                )
                return

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

        runtime = None
        admission_reserved = False
        if self._is_generation_create(scope):
            app = scope.get("app")
            runtime = getattr(getattr(app, "state", None), "runtime", None)
            if runtime is not None:
                idempotency_key = headers.get(b"idempotency-key", b"").decode("latin-1") or None
                allowed, admission_reserved = await runtime.try_reserve_job_admission(idempotency_key)
                if not allowed:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        503,
                        "JOB_QUEUE_FULL",
                        "The Provider job queue is at capacity. Retry after queued work drains.",
                        response_headers={"Retry-After": "10"},
                        retryable=True,
                    )
                    return

        try:
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
        finally:
            if admission_reserved and runtime is not None:
                await runtime.release_job_admission()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    runtime = build_runtime(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.process_lease import SingleProcessLease
        lease = SingleProcessLease(settings.project_store_path) if settings.env == "production" else None
        try:
            if lease:
                lease.acquire()
            if runtime.worker:
                await runtime.worker.start()
            yield
        finally:
            try:
                if runtime.worker:
                    await runtime.worker.stop()
                await runtime.bridge.close_background_tasks()
            finally:
                try:
                    runtime.projects.close()
                finally:
                    if lease:
                        lease.release()

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

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "API key",
            "description": "Required for /v1 business endpoints in production.",
        }
        methods = {"get", "post", "put", "patch", "delete", "options", "head"}
        for path, path_item in schema.get("paths", {}).items():
            if path == "/v1" or path.startswith("/v1/"):
                for method, operation in path_item.items():
                    if method.lower() in methods and isinstance(operation, dict):
                        operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
    return app


app = create_app()
