from fastapi import APIRouter, Depends, Request
from app.api.deps import get_admin_client
router=APIRouter(prefix="/v1/accounts",tags=["Provider accounts"])

@router.get("")
def list_accounts(request: Request,_client=Depends(get_admin_client)):
    return {"object":"list","data":request.app.state.runtime.bridge.list_accounts(),"has_more":False,"next_cursor":None}
