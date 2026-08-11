from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_admin_client
from app.api.errors import APIError

router=APIRouter(prefix="/v1/extensions",tags=["Extensions"])


def _manager(request: Request):
    return request.app.state.runtime.extension_manager


def _require_known(request: Request, installation_id: str):
    item=_manager(request).get(installation_id)
    if not item:
        raise APIError(404,"EXTENSION_NOT_FOUND","The requested extension installation is not known.",param="installation_id")
    return item


def _raise_control_error(result: dict):
    error=result.get("error") if isinstance(result,dict) else None
    if not error:return
    status=409 if error=="extension_offline" else 502
    code="EXTENSION_OFFLINE" if error=="extension_offline" else "EXTENSION_CONTROL_ERROR"
    raise APIError(status,code,str(error),retryable=error!="extension_offline")


@router.get("")
def list_extensions(request: Request,_client=Depends(get_admin_client)):
    return {"object":"list","data":_manager(request).list(),"has_more":False,"next_cursor":None}


@router.get("/{installation_id}")
def get_extension(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    return _require_known(request,installation_id)


@router.post("/{installation_id}/pause")
def pause_extension(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    return _manager(request).pause(installation_id)


@router.post("/{installation_id}/resume")
def resume_extension(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    return _manager(request).resume(installation_id)


@router.post("/{installation_id}/ping")
async def ping_extension(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    result=await _manager(request).ping(installation_id);_raise_control_error(result);return result


@router.post("/{installation_id}/refresh-auth")
async def refresh_extension_auth(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    result=await _manager(request).refresh_auth(installation_id);_raise_control_error(result);return result


@router.post("/{installation_id}/open-flow")
async def open_flow(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    result=await _manager(request).open_flow(installation_id);_raise_control_error(result);return result


@router.post("/{installation_id}/reconnect")
async def reconnect_extension(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    result=await _manager(request).reconnect(installation_id);_raise_control_error(result);return result


@router.get("/{installation_id}/diagnostics")
async def extension_diagnostics(installation_id: str,request: Request,_client=Depends(get_admin_client)):
    _require_known(request,installation_id)
    result=await _manager(request).diagnostics(installation_id)
    if not result:raise APIError(404,"EXTENSION_NOT_FOUND","The requested extension installation is not known.")
    return result
