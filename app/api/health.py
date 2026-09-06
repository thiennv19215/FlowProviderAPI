from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["Health"])


def _status(request: Request) -> dict:
    runtime = request.app.state.runtime
    bridge = runtime.bridge
    try:
        store_ready = runtime.projects.check()
        job_counts = runtime.durable_job_status_counts() if store_ready else {
            "queued": 0,
            "dispatching": 0,
            "running": 0,
        }
    except Exception:
        store_ready = False
        job_counts = {"queued": 0, "dispatching": 0, "running": 0}

    ready_conns = bridge.ready_connections()
    provider_accounts = len(ready_conns)
    if not store_ready:
        status = "unavailable"
    elif provider_accounts == 0:
        status = "waiting_for_provider"
    else:
        status = "ready"

    queue_capacity = int(getattr(runtime.settings, "job_queue_max_active", 200))
    active_jobs = sum(job_counts.values())
    return {
        "status": status,
        "project_store": "ready" if store_ready else "unavailable",
        "provider_accounts": provider_accounts,
        "video_lite_ready_accounts": sum(
            1 for connection in ready_conns
            if runtime.can_reserve(connection, 20)
        ),
        "jobs": job_counts,
        "active_jobs": active_jobs,
        "job_queue_capacity": queue_capacity,
        "job_queue_remaining": max(0, queue_capacity - active_jobs),
    }


@router.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def ready(request: Request):
    status = _status(request)
    if status["status"] != "ready":
        return JSONResponse(status_code=503, content=status)
    return status


@router.get("/api/health", include_in_schema=False)
def extension_health(request: Request):
    status = _status(request)
    return {"ok": status["status"] != "unavailable", **status}
