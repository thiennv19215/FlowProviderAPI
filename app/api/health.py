from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["Health"])


def _status(request: Request) -> dict:
    runtime = request.app.state.runtime
    bridge = runtime.bridge
    try:
        store_ready = runtime.projects.check()
    except Exception:
        store_ready = False
    ready_conns = bridge.ready_connections()
    provider_accounts = len(ready_conns)
    if not store_ready:
        status = "unavailable"
    elif provider_accounts == 0:
        status = "waiting_for_provider"
    else:
        status = "ready"
    accounts_info = [
        {
            "id": connection.id,
            "installation_id": connection.installation_id,
            "account_email": getattr(connection, "account_email", None),
            "credits": getattr(connection, "credits", None),
            "available_credits": runtime.available_credits(connection),
            "can_reserve_20": runtime.can_reserve(connection, 20),
            "last_error": getattr(connection, "last_error", None),
        }
        for connection in ready_conns
    ]
    return {
        "status": status,
        "project_store": "ready" if store_ready else "unavailable",
        "provider_accounts": provider_accounts,
        "video_lite_ready_accounts": sum(
            1 for connection in ready_conns
            if runtime.can_reserve(connection, 20)
        ),
        "accounts": accounts_info,
    }


@router.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def ready(request: Request):
    status = _status(request)
    if status["status"] == "unavailable":
        return JSONResponse(status_code=503, content=status)
    return status


@router.get("/api/health", include_in_schema=False)
def extension_health(request: Request):
    status = _status(request)
    return {"ok": status["status"] != "unavailable", **status}
