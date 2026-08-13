from fastapi import APIRouter, Request

router = APIRouter(tags=["Health"])


def _status(request: Request) -> dict:
    bridge = request.app.state.runtime.bridge
    return {
        "status": "ready",
        "provider_accounts": len(bridge.ready_connections()),
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
