from fastapi import APIRouter, Request

router = APIRouter(tags=["Health"])


def _status(request: Request) -> dict:
    runtime = request.app.state.runtime
    bridge = runtime.bridge
    try:
        store_ready = runtime.projects.check()
    except Exception:
        store_ready = False
    provider_accounts = len(bridge.ready_connections())
    if not store_ready:
        status = "unavailable"
    elif provider_accounts == 0:
        status = "waiting_for_provider"
    else:
        status = "ready"
    return {
        "status": status,
        "project_store": "ready" if store_ready else "unavailable",
        "provider_accounts": provider_accounts,
        "video_lite_ready_accounts": len(bridge.ready_connections(min_credits=20)),
    }


@router.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def ready(request: Request):
    return _status(request)


@router.get("/api/health", include_in_schema=False)
def extension_health(request: Request):
    status = _status(request)
    return {"ok": True, **status}
