from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router=APIRouter(tags=["Health"])

@router.get("/v1/health")
def health(request:Request):
    bridge=request.app.state.runtime.bridge
    return {"status":"ok","extension_connected":bridge.connected,"ready_accounts":len(bridge.ready_connections())}

@router.get("/health/live",include_in_schema=False)
def live():return {"status":"ok"}

@router.get("/health/ready",include_in_schema=False)
async def ready(request:Request):
    runtime=request.app.state.runtime;db_ok=False
    try:
        with runtime.session_factory() as db:
            db.execute(text("SELECT 1"));db_ok=True
    except Exception:db_ok=False
    storage_ok=await runtime.storage.healthcheck()
    is_ready=db_ok and storage_ok
    payload={"status":"ready" if is_ready else "not_ready","database":db_ok,"storage":storage_ok,"provider_accounts":len(runtime.bridge.ready_connections()),"storage_backend":runtime.settings.storage_backend}
    return payload if is_ready else JSONResponse(status_code=503,content=payload)

@router.get("/api/health",include_in_schema=False)
def extension_health(request:Request):
    bridge=request.app.state.runtime.bridge
    return {"ok":True,"extension_connected":bridge.connected,"ready_accounts":len(bridge.ready_connections())}
