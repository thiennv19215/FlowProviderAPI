from __future__ import annotations

import asyncio
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


def _supports_protocol(websocket:WebSocket)->bool:
    raw=websocket.headers.get("sec-websocket-protocol") or ""
    protocols=[item.strip() for item in raw.split(",") if item.strip()]
    return "flow-provider-v7" in protocols


async def _serve(websocket:WebSocket):
    runtime=websocket.app.state.runtime;bridge=runtime.bridge;manager=runtime.extension_manager
    has_protocol=_supports_protocol(websocket)
    await websocket.accept(subprotocol="flow-provider-v7" if has_protocol else None)
    adapter=SocketAdapter(websocket);conn=None;heartbeat_task=None
    try:
        if not has_protocol:
            await websocket.close(4406,"extension subprotocol required");return
        hello=await receive_json(websocket,HELLO_TIMEOUT)
        if hello.get("type")!="extension_ready":await websocket.close(4400,"extension_ready frame required");return
        if hello.get("protocolVersion") not in PROTOCOL_VERSIONS:await websocket.close(4400,"extension protocol mismatch");return
        expected_key=runtime.settings.extension_api_key
        supplied_key=str(hello.get("connectorApiKey") or "")
        if expected_key and not hmac.compare_digest(expected_key,supplied_key):
            await websocket.close(4401,"extension authentication failed");return
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
