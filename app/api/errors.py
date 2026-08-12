from __future__ import annotations

import logging
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorResponse

logger=logging.getLogger(__name__)

class APIError(Exception):
    def __init__(self,status_code:int,code:str,message:str,*,field:str|None=None,retryable:bool=False,headers:dict[str,str]|None=None):
        self.status_code=status_code;self.code=code;self.message=message;self.field=field;self.retryable=retryable;self.headers=headers or {}


def error_body(request:Request,*,status_code:int,code:str,message:str,details:list[dict]|None=None,retryable:bool=False):
    return {"error":{"status_code":status_code,"code":code,"message":message,"details":details or [],"request_id":getattr(request.state,"request_id",None),"retryable":retryable}}

async def api_error_handler(request:Request,exc:APIError):
    details=[{"field":exc.field,"code":exc.code,"message":exc.message}] if exc.field else []
    return JSONResponse(status_code=exc.status_code,content=error_body(request,status_code=exc.status_code,code=exc.code,message=exc.message,details=details,retryable=exc.retryable),headers=exc.headers)


def _validation_code(error_type:str)->str:
    if error_type=="missing":return "REQUIRED_FIELD"
    if error_type=="literal_error":return "INVALID_CHOICE"
    if error_type=="extra_forbidden":return "UNKNOWN_FIELD"
    if error_type=="json_invalid":return "INVALID_JSON"
    if "too_short" in error_type or "too_long" in error_type:return "INVALID_LENGTH"
    if error_type.startswith(("greater_than","less_than")):return "OUT_OF_RANGE"
    if error_type.endswith(("_type","_parsing")):return "INVALID_TYPE"
    return "INVALID_VALUE"


def _validation_field(loc:list|tuple)->str|None:
    parts=[str(value) for value in loc if value not in {"body","query","path","header"}]
    return ".".join(parts) or (str(loc[0]) if loc else None)

async def validation_error_handler(request:Request,exc:RequestValidationError):
    errors=exc.errors();invalid_json=any(item.get("type")=="json_invalid" for item in errors)
    details=[{"field":_validation_field(item.get("loc") or []),"code":_validation_code(item.get("type") or ""),"message":item.get("msg") or "Invalid value."} for item in errors]
    status_code=400 if invalid_json else 422
    return JSONResponse(status_code=status_code,content=error_body(request,status_code=status_code,code="INVALID_JSON" if invalid_json else "VALIDATION_ERROR",message="Request body is not valid JSON." if invalid_json else "Request validation failed.",details=details))


async def http_error_handler(request:Request,exc:StarletteHTTPException):
    codes={404:"ENDPOINT_NOT_FOUND",405:"METHOD_NOT_ALLOWED"};messages={404:"The requested endpoint does not exist.",405:"The HTTP method is not allowed for this endpoint."}
    code=codes.get(exc.status_code,f"HTTP_{exc.status_code}");message=messages.get(exc.status_code,exc.detail if isinstance(exc.detail,str) else "The request could not be completed.")
    return JSONResponse(status_code=exc.status_code,content=error_body(request,status_code=exc.status_code,code=code,message=message),headers=exc.headers)

async def unexpected_error_handler(request:Request,exc:Exception):
    logger.exception("unhandled API error request_id=%s",getattr(request.state,"request_id",None),exc_info=exc)
    return JSONResponse(status_code=500,content=error_body(request,status_code=500,code="INTERNAL_ERROR",message="An unexpected internal error occurred.",retryable=True))


PUBLIC_ERROR_RESPONSES={
    400:{"model":ErrorResponse,"description":"Malformed or invalid request"},
    401:{"model":ErrorResponse,"description":"Authentication failed"},
    403:{"model":ErrorResponse,"description":"Permission denied"},
    404:{"model":ErrorResponse,"description":"Resource not found"},
    409:{"model":ErrorResponse,"description":"Resource state conflict"},
    413:{"model":ErrorResponse,"description":"Request payload too large"},
    422:{"model":ErrorResponse,"description":"Request validation failed"},
    429:{"model":ErrorResponse,"description":"Rate limit exceeded"},
    500:{"model":ErrorResponse,"description":"Internal server error"},
    502:{"model":ErrorResponse,"description":"Upstream provider error"},
    503:{"model":ErrorResponse,"description":"Service unavailable"},
}
