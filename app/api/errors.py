from __future__ import annotations

import logging
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger=logging.getLogger(__name__)

class APIError(Exception):
    def __init__(self,status_code:int,code:str,message:str,*,error_type:str="api_error",param:str|None=None,retryable:bool=False):
        self.status_code=status_code;self.code=code;self.message=message;self.error_type=error_type;self.param=param;self.retryable=retryable


def error_body(request:Request,*,code:str,message:str,error_type:str,param:str|None=None,retryable:bool=False):
    return {"error":{"code":code,"message":message,"type":error_type,"param":param,"request_id":getattr(request.state,"request_id",None),"retryable":retryable}}

async def api_error_handler(request:Request,exc:APIError):
    return JSONResponse(status_code=exc.status_code,content=error_body(request,code=exc.code,message=exc.message,error_type=exc.error_type,param=exc.param,retryable=exc.retryable))

async def validation_error_handler(request:Request,exc:RequestValidationError):
    first=exc.errors()[0] if exc.errors() else {};loc=first.get("loc") or [];param=".".join(str(v) for v in loc if v not in {"body","query","path"}) or None
    return JSONResponse(status_code=422,content=error_body(request,code="VALIDATION_ERROR",message=first.get("msg") or "Request validation failed.",error_type="validation_error",param=param,retryable=False))

async def unexpected_error_handler(request:Request,exc:Exception):
    logger.exception("unhandled API error request_id=%s",getattr(request.state,"request_id",None),exc_info=exc)
    return JSONResponse(status_code=500,content=error_body(request,code="INTERNAL_ERROR",message="An unexpected internal error occurred.",error_type="server_error",retryable=True))
