from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router=APIRouter()
logger=logging.getLogger(__name__)
PROTOCOL_VERSIONS={7}
HELLO_TIMEOUT=10
MAX_FRAME_CHARS=2*1024*1024


class SocketAdapter:
    def __init__(self,ws: WebSocket):self.websocket=ws
    async def send(self,payload: str):await self.websocket.send_text(payload)
    async def close(self,code: int=1000,reason: str=""):await self.websocket.close(code=code,reason=reason)


async def receive_json(ws: WebSocket,timeout: float):
    raw=await asyncio.wait_for(ws.receive_text(),timeout=timeout)
    if len(raw)>MAX_FRAME_CHARS:raise ValueError("frame_too_large")
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError("object_frame_required")
    return data


async def _serve(websocket: WebSocket):
    bridge=websocket.app.state.runtime.bridge
    await websocket.accept();adapter=SocketAdapter(websocket);conn=None
    try:
        hello=await receive_json(websocket,HELLO_TIMEOUT)
        if hello.get("type")!="extension_ready":await websocket.close(4400,"extension_ready frame required");return
        if hello.get("protocolVersion") not in PROTOCOL_VERSIONS:await websocket.close(4400,"extension protocol mismatch");return
        installation=str(hello.get("installationId") or "").strip()
        if not installation:await websocket.close(4400,"installation id required");return
        prior=bridge.get_connection_by_installation(installation)
        if prior:
            try:await prior.ws.close(4000,"superseded_by_new_connection")
            except Exception:pass
            bridge.clear(connection_id=prior.id)
        conn=bridge.register(adapter,hello);await bridge.handle_message(hello,adapter)
        logger.info("provider extension connected installation=%s",installation)
        while True:
            data=await receive_json(websocket,bridge.DEFAULT_TIMEOUT*2)
            await bridge.handle_message(data,adapter)
    except WebSocketDisconnect:pass
    except asyncio.TimeoutError:
        try:await websocket.close(4408,"connection idle timeout")
        except Exception:pass
    except (ValueError,json.JSONDecodeError):
        try:await websocket.close(4400,"invalid websocket frame")
        except Exception:pass
    except Exception:
        logger.exception("extension gateway failure")
        try:await websocket.close(1011,"extension gateway failure")
        except Exception:pass
    finally:
        bridge.clear(adapter)


@router.websocket("/api/extensions/ws")
async def extension_ws(websocket: WebSocket):await _serve(websocket)

@router.websocket("/v1/extensions/ws")
async def extension_ws_v1(websocket: WebSocket):await _serve(websocket)
