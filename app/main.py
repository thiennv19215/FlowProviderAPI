from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.accounts import router as accounts_router
from app.api.assets import delivery_router as media_delivery_router, router as assets_router
from app.api.errors import APIError, PUBLIC_ERROR_RESPONSES, api_error_handler, http_error_handler, unexpected_error_handler, validation_error_handler
from app.api.extensions import router as extensions_admin_router
from app.api.generations import router as generations_router
from app.api.health import router as health_router
from app.api.jobs import router as tasks_router
from app.config import Settings, get_settings
from app.auth.api_keys import ensure_bootstrap_client
from app.extension.gateway import router as extension_router
from app.jobs.repository import recover_expired
from app.runtime import build_runtime

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings:Settings|None=None,*,extra_providers:list|None=None)->FastAPI:
    settings=settings or get_settings();runtime=build_runtime(settings,extra_providers=extra_providers)

    @asynccontextmanager
    async def lifespan(app:FastAPI):
        with runtime.session_factory() as db:
            ensure_bootstrap_client(db,settings.bootstrap_api_key);recover_expired(db)
        if settings.worker_enabled and runtime.worker:await runtime.worker.start()
        yield
        if runtime.worker:await runtime.worker.stop()
        runtime.engine.dispose()

    app=FastAPI(title="Flow Provider API",version="1.0.0",description="Developer-facing asynchronous AI media generation API.",lifespan=lifespan,responses=PUBLIC_ERROR_RESPONSES)
    app.state.runtime=runtime

    @app.middleware("http")
    async def request_id_middleware(request:Request,call_next):
        request.state.request_id=request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex}"
        try:
            if request.method=="POST" and request.url.path=="/v1/media":
                # Bound the body before FastAPI's multipart parser can spool an
                # unbounded upload to its temporary directory. A small allowance
                # covers multipart headers and boundaries around the file bytes.
                limit=settings.max_upload_bytes+1024*1024
                content_length=request.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length)>limit:raise APIError(413,"MEDIA_TOO_LARGE",f"Media exceeds the {settings.max_upload_bytes} byte upload limit.",field="file")
                    except ValueError:raise APIError(400,"INVALID_CONTENT_LENGTH","Content-Length must be an integer.",field="Content-Length")
                receive=request._receive;received=0
                async def limited_receive():
                    nonlocal received
                    message=await receive()
                    if message["type"]=="http.request":
                        received+=len(message.get("body",b""))
                        if received>limit:raise APIError(413,"MEDIA_TOO_LARGE",f"Media exceeds the {settings.max_upload_bytes} byte upload limit.",field="file")
                    return message
                request._receive=limited_receive
            response=await call_next(request)
        except APIError as exc:
            response=await api_error_handler(request,exc)
        response.headers["X-Request-Id"]=request.state.request_id
        if hasattr(request.state,"rate_limit"):
            limit,remaining,reset=request.state.rate_limit
            response.headers["X-RateLimit-Limit"]=str(limit);response.headers["X-RateLimit-Remaining"]=str(remaining);response.headers["X-RateLimit-Reset"]=str(reset)
        return response

    app.add_exception_handler(APIError,api_error_handler)
    app.add_exception_handler(RequestValidationError,validation_error_handler)
    app.add_exception_handler(StarletteHTTPException,http_error_handler)
    app.add_exception_handler(Exception,unexpected_error_handler)
    for router in (health_router,generations_router,tasks_router,assets_router,media_delivery_router,accounts_router,extensions_admin_router,extension_router):app.include_router(router)
    return app

app=create_app()
