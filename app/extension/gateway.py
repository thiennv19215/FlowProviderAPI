from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router=APIRouter();logger=logging.getLogger(__name__)
PROTOCOL_VERSIONS={7};HELLO_TIMEOUT=10;MAX_FRAME_CHARS=2*1024*1024;MAX_INSTALLATION_ID_CHARS=120


class SocketAdapter:
    def __init__(self,ws:WebSocket):self.websocket=ws
    async def send(self,payload:str):await self.websocket.send_text(payload)
    async def close(self,code:int=1000,reason:str=""):await self.websocket.close(code=code,reason=reason)


async def receive_json(ws:WebSocket,timeout:float):
    raw=await asyncio.wait_for(ws.receive_text(),timeout=timeout)
    if len(raw)>MAX_FRAME_CHARS:raise ValueError("frame_too_large")
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError("object_frame_required")
    return data


async def heartbeat_loop(conn,bridge,manager,settings):
    interval=settings.extension_heartbeat_seconds;grace=settings.extension_heartbeat_grace_seconds
    while bridge.get(conn.id) is conn:
        await asyncio.sleep(interval)
        if bridge.get(conn.id) is not conn:return
        response=await bridge.send_rpc(conn.id,"PING",{},timeout=min(10,max(3,grace)))
        if not response.get("error"):
            bridge.mark_healthy(conn.id);manager.heartbeat(conn);continue
        current=bridge.mark_suspect(conn.id)
        if current is not conn:return
        manager.suspect(conn);marker=conn.suspect_since
        await asyncio.sleep(grace)
        if bridge.get(conn.id) is not conn:return
        if conn.suspect_since is None or conn.suspect_since!=marker:
            manager.heartbeat(conn);continue
        try:await conn.ws.close(4408,"extension heartbeat timeout")
        except Exception:pass
        return


def _presented_gateway_token(websocket:WebSocket)->tuple[str|None,bool]:
    raw=websocket.headers.get("sec-websocket-protocol") or ""
    protocols=[item.strip() for item in raw.split(",") if item.strip()]
    for protocol in protocols:
        if not protocol.startswith("flow-token."):continue
        encoded=protocol[len("flow-token."):]
        try:
            padded=encoded+"="*((4-len(encoded)%4)%4)
            token=base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            return token,"flow-provider-v7" in protocols
        except Exception:return None,"flow-provider-v7" in protocols
    return None,"flow-provider-v7" in protocols


def _gateway_token_valid(expected:str|None,presented:str|None)->bool:
    if not expected:return True
    if not presented:return False
    return hmac.compare_digest(expected,presented)


async def _serve(websocket:WebSocket):
    runtime=websocket.app.state.runtime;bridge=runtime.bridge;manager=runtime.extension_manager
    presented_token,has_protocol=_presented_gateway_token(websocket)
    await websocket.accept(subprotocol="flow-provider-v7" if has_protocol else None)
    adapter=SocketAdapter(websocket);conn=None;heartbeat_task=None
    try:
        if not _gateway_token_valid(runtime.settings.extension_gateway_token,presented_token):
            await websocket.close(4401,"extension gateway authentication failed");return
        hello=await receive_json(websocket,HELLO_TIMEOUT)
        if hello.get("type")!="extension_ready":await websocket.close(4400,"extension_ready frame required");return
        if hello.get("protocolVersion") not in PROTOCOL_VERSIONS:await websocket.close(4400,"extension protocol mismatch");return
        installation=str(hello.get("installationId") or "").strip()
        if not installation:await websocket.close(4400,"installation id required");return
        if len(installation)>MAX_INSTALLATION_ID_CHARS:await websocket.close(4400,"installation id too long");return
        prior=bridge.get_connection_by_installation(installation)
        if prior:
            manager.disconnected(prior)
            try:await prior.ws.close(4000,"superseded_by_new_connection")
            except Exception:pass
            bridge.clear(connection_id=prior.id)
        conn=bridge.register(adapter,hello);manager.connected(conn);await bridge.handle_message(hello,adapter)
        heartbeat_task=asyncio.create_task(heartbeat_loop(conn,bridge,manager,runtime.settings),name=f"extension-heartbeat-{conn.id}")
        logger.info("provider extension connected installation=%s",installation)
        while True:
            data=await receive_json(websocket,bridge.DEFAULT_TIMEOUT*2);await bridge.handle_message(data,adapter)
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
        if heartbeat_task:
            heartbeat_task.cancel();await asyncio.gather(heartbeat_task,return_exceptions=True)
        if conn:manager.disconnected(conn)
        bridge.clear(adapter)


@router.websocket("/api/extensions/ws")
async def extension_ws(websocket:WebSocket):await _serve(websocket)

@router.websocket("/v1/extensions/ws")
async def extension_ws_v1(websocket:WebSocket):await _serve(websocket)
