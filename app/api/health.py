from fastapi import APIRouter, Request
router=APIRouter(tags=["Health"])

@router.get("/v1/health")
def health(request: Request):
    bridge=request.app.state.runtime.bridge
    return {"status":"ok","extension_connected":bridge.connected,"ready_accounts":len(bridge.ready_connections())}

@router.get("/health/live")
def live():return {"status":"ok"}

@router.get("/health/ready")
def ready(request: Request):return {"status":"ready","provider_accounts":len(request.app.state.runtime.bridge.ready_connections())}

@router.get("/api/health")
def extension_health(request: Request):
    bridge=request.app.state.runtime.bridge
    return {"ok":True,"extension_connected":bridge.connected,"ready_accounts":len(bridge.ready_connections())}
