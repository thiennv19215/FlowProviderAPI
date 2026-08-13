from __future__ import annotations

from collections.abc import Callable

from fastapi import Response
from fastapi.responses import JSONResponse, PlainTextResponse


def upstream_response(result: dict, error: Callable[[dict], Exception]) -> Response:
    status = result.get("status")
    if not isinstance(status, int) or not 100 <= status <= 599:
        if result.get("error"):
            raise error(result)
        raise error({"error": "extension_response_invalid"})
    blocked = {"connection", "content-encoding", "content-length", "keep-alive", "set-cookie", "transfer-encoding"}
    headers = {str(key): str(value) for key, value in (result.get("headers") or {}).items() if str(key).lower() not in blocked}
    headers["X-Flow-Upstream-Status"] = str(status)
    if "data" in result:
        return JSONResponse(status_code=status, content=result["data"], headers=headers)
    if isinstance(result.get("text"), str):
        return PlainTextResponse(status_code=status, content=result["text"], headers=headers)
    return Response(status_code=status, headers=headers)
