from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router=APIRouter(tags=["Health"])

@router.get("/v1/health")
def health(request:Request):
    bridge=request.app.state.runtime.bridge
    return {"status":"ok","extension_connected":bridge.connected,"ready_accounts":len(bridge.ready_connections())}

@router.get("/health/live")
def live():return {"status":"ok"}

@router.get("/health/ready")
def ready(request:Request):
    runtime=request.app.state.runtime;db_ok=False
    try:
        with runtime.session_factory() as db:
            db.execute(text("SELECT 1"));db_ok=True
    except Exception:db_ok=False
    payload={"status":"ready" if db_ok else "not_ready","database":db_ok,"provider_accounts":len(runtime.bridge.ready_connections()),"storage_backend":runtime.settings.storage_backend}
    return payload if db_ok else JSONResponse(status_code=503,content=payload)

@router.get("/api/health")
def extension_health(request:Request):
    bridge=request.app.state.runtime.bridge
    return {"ok":True,"extension_connected":bridge.connected,"ready_accounts":len(bridge.ready_connections())}
