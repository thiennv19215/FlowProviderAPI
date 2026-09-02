from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

logger=logging.getLogger(__name__)
RECAPTCHA_FALLBACK_KEY="6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
FLOW_CREDITS_URL="https://aisandbox-pa.googleapis.com/v1/credits"
SUPPORTED_PAYGATE_TIERS={"PAYGATE_TIER_ONE","PAYGATE_TIER_TWO"}
MEDIA_DOWNLOAD_HOSTS=("flow-content.google","storage.googleapis.com")
MAX_MEDIA_DOWNLOAD_BYTES=48*1024*1024


def resolve_paygate_tier(payload:dict)->str|None:
    tier=payload.get("userPaygateTier")
    if tier in SUPPORTED_PAYGATE_TIERS:return tier
    # Current freemium accounts omit the legacy tier while using tier-one models.
    if payload.get("sku")=="G1_FREEMIUM":return "PAYGATE_TIER_ONE"
    return None


@dataclass
class ExtensionConnection:
    id: str
    ws: Any
    installation_id: str
    runtime_id: str
    profile_id: str
    profile_name: str
    connected_at: float=field(default_factory=time.time)
    last_seen_at: float=field(default_factory=time.time)
    flow_key: str|None=None
    auth_generation: int = 0
    flow_api_key: str|None=None
    user_info: dict|None=None
    account_email: str|None=None
    paygate_tier: str|None=None
    sku: str|None=None
    credits: int|None=None
    max_slots: int=3
    cooldown_until: float|None=None
    cooldown_reason: str|None=None
    request_count: int=0
    success_count: int=0
    failed_count: int=0
    last_error: str|None=None
    extension_connection_id: str|None=None
    simulation_mode: bool=False
    suspect_since: float|None=None

    @property
    def ready(self)->bool:
        return bool(self.simulation_mode or (self.flow_key and self.account_email and self.paygate_tier and self.suspect_since is None))

    @property
    def health_status(self)->str:
        return "suspect" if self.suspect_since is not None else "online"


class FlowBridge:
    DEFAULT_TIMEOUT=180.0;BEARER_TIMEOUT=30.0;TAB_TIMEOUT=30.0;CAPTCHA_TIMEOUT=45.0

    def __init__(self,*,flow_api_key:str|None,slot_capacity:int=3,cooldown_seconds:int=180):
        self.flow_api_key=flow_api_key;self.slot_capacity=slot_capacity;self.cooldown_seconds=cooldown_seconds
        self._connections:dict[str,ExtensionConnection]={}
        self._installation_to_id:dict[str,str]={}
        self._ws_to_id:dict[int,str]={}
        self._pending:dict[str,tuple[asyncio.Future,str]]={}
        self._refresh_tasks:dict[str,asyncio.Task]={}

    def schedule_account_refresh(self,connection_id:str,*,initial_delay:float=0)->None:
        current=self._refresh_tasks.get(connection_id)
        if current and not current.done():return

        async def runner():
            try:
                delays=(initial_delay,1,5,15)
                attempt=0
                while True:
                    delay=delays[attempt] if attempt<len(delays) else 60
                    if delay:await asyncio.sleep(delay)
                    conn=self.get(connection_id)
                    if not conn or not conn.flow_key:return
                    refresh_task=asyncio.current_task()
                    generation=conn.auth_generation
                    await self.refresh_account(connection_id)
                    # An account switch may replace this refresh task while
                    # the old request is still completing. Never let the old
                    # runner continue or schedule another refresh for it.
                    if self._refresh_tasks.get(connection_id) is not refresh_task:return
                    conn=self.get(connection_id)
                    if not conn or not conn.flow_key or isinstance(conn.credits,int):return
                    if conn.auth_generation != generation:return
                    attempt+=1
            finally:
                if self._refresh_tasks.get(connection_id) is asyncio.current_task():
                    self._refresh_tasks.pop(connection_id,None)

        self._refresh_tasks[connection_id]=asyncio.create_task(
            runner(),name=f"flow-credit-refresh-{connection_id}"
        )

    async def close_background_tasks(self)->None:
        tasks=list(self._refresh_tasks.values())
        self._refresh_tasks.clear()
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    @property
    def connected(self)->bool:return bool(self._connections)

    @staticmethod
    def stable_account_id(installation_id:str)->str:return installation_id[:120]

    def connections(self)->list[ExtensionConnection]:return list(self._connections.values())
    def connected_count(self)->int:return len(self._connections)
    def pending_count(self,connection_id:str)->int:return sum(1 for _req_id,(_future,cid) in self._pending.items() if cid==connection_id)

    def get_connection_by_installation(self,installation_id:str)->ExtensionConnection|None:
        cid=self._installation_to_id.get(installation_id);return self._connections.get(cid) if cid else None

    def register(self,ws:Any,hello:dict)->ExtensionConnection:
        installation=str(hello.get("installationId") or "").strip()
        if not installation:raise ValueError("installation_id_required")
        cid=self.stable_account_id(installation);prior=self._connections.get(cid)
        if prior:self.clear(connection_id=cid)
        conn=ExtensionConnection(id=cid,ws=ws,installation_id=installation,runtime_id=str(hello.get("runtimeId") or "chrome")[:40],profile_id=str(hello.get("profileId") or installation)[:128],profile_name=str(hello.get("profileName") or "Browser extension")[:160],max_slots=self.slot_capacity,extension_connection_id=str(hello.get("connectionId") or "")[:128] or None,simulation_mode=hello.get("simulationMode") is True)
        self._connections[cid]=conn;self._installation_to_id[installation]=cid;self._ws_to_id[id(ws)]=cid;return conn

    def clear(self,ws:Any|None=None,*,connection_id:str|None=None)->None:
        if ws is not None:connection_id=self._ws_to_id.get(id(ws))
        if not connection_id:return
        conn=self._connections.pop(connection_id,None)
        if not conn:return
        self._installation_to_id.pop(conn.installation_id,None);self._ws_to_id.pop(id(conn.ws),None)
        for req_id,(future,cid) in list(self._pending.items()):
            if cid==connection_id:
                self._pending.pop(req_id,None)
                if not future.done():future.set_exception(ConnectionError("extension_disconnected"))

    def get(self,connection_id:str)->ExtensionConnection|None:return self._connections.get(connection_id)

    def mark_suspect(self,connection_id:str)->ExtensionConnection|None:
        conn=self.get(connection_id)
        if conn and conn.suspect_since is None:conn.suspect_since=time.time()
        return conn

    def mark_healthy(self,connection_id:str)->ExtensionConnection|None:
        conn=self.get(connection_id)
        if conn:conn.suspect_since=None;conn.last_seen_at=time.time()
        return conn

    def invalidate_auth(self,connection_id:str,reason:str|None=None)->None:
        conn=self.get(connection_id)
        if conn:self._invalidate_auth(conn,reason)

    async def send_auth_ack(self,connection_id:str)->None:
        conn=self.get(connection_id)
        if conn:await self._send_auth_ack(conn)

    def list_accounts(self)->list[dict]:
        now=time.time();result=[]
        for c in sorted(self._connections.values(),key=lambda x:x.connected_at):
            cooldown=max(0,int(c.cooldown_until-now)) if c.cooldown_until and c.cooldown_until>now else 0
            result.append({"id":c.id,"installation_id":c.installation_id,"runtime_id":c.runtime_id,"profile_name":c.profile_name,"profile_id":c.profile_id,"email":c.account_email,"connected":True,"ready":c.ready,"health_status":c.health_status,"simulation_mode":c.simulation_mode,"paygate_tier":c.paygate_tier,"sku":c.sku,"credits":c.credits,"slot_capacity":c.max_slots,"cooldown_remaining_s":cooldown,"cooldown_reason":c.cooldown_reason,"last_seen_at":c.last_seen_at,"request_count":c.request_count,"success_count":c.success_count,"failed_count":c.failed_count,"last_error":c.last_error})
        return result

    def ready_connections(self,*,min_credits:int=0)->list[ExtensionConnection]:
        now=time.time();out=[]
        for c in self._connections.values():
            if not c.ready:continue
            if c.cooldown_until and c.cooldown_until>now:continue
            if min_credits and (c.credits is None or c.credits<min_credits):continue
            out.append(c)
        return sorted(out,key=lambda c:c.connected_at)

    def _invalidate_auth(self,conn:ExtensionConnection,reason:str|None=None)->None:
        conn.flow_key=None;conn.paygate_tier=None;conn.sku=None;conn.credits=None;conn.last_error=(reason or "authentication_required")[:300]

    async def handle_message(self,data:dict,ws:Any)->None:
        conn_id=self._ws_to_id.get(id(ws));conn=self._connections.get(conn_id) if conn_id else None;msg_type=data.get("type")
        if conn:conn.last_seen_at=time.time();conn.suspect_since=None
        if msg_type=="token_captured" and conn:
            token=data.get("flowKey")
            if isinstance(token,str) and token:conn.flow_key=token;self.schedule_account_refresh(conn.id)
            return
        if msg_type=="flow_api_key" and conn:
            api_key=data.get("apiKey")
            if isinstance(api_key,str) and 20<=len(api_key)<=100 and all(ch.isalnum() or ch in "-_" for ch in api_key):
                changed=api_key!=conn.flow_api_key
                conn.flow_api_key=api_key
                if conn.flow_key and changed:self.schedule_account_refresh(conn.id)
            return
        if msg_type=="user_info" and conn:
            info=data.get("userInfo")
            if isinstance(info,dict):
                conn.user_info={k:info.get(k) for k in ("email","name","picture","verified_email") if k in info};email=info.get("email");conn.account_email=email.strip().lower() if isinstance(email,str) and email.strip() else None;await self._send_auth_ack(conn)
            return
        if msg_type=="auth_sync_status" and conn:
            status=str(data.get("status") or "")
            if status in {"needs_labs_sign_in","signed_out","auth_error"}:self._invalidate_auth(conn,str(data.get("reason") or status));await self._send_auth_ack(conn)
            return
        if msg_type=="simulation_mode_changed" and conn:
            conn.simulation_mode=data.get("simulationMode") is True
            return
        if msg_type in {"pong", "ping"}:
            return
        req_id=data.get("id")
        if req_id and req_id in self._pending:
            future,cid=self._pending.pop(req_id)
            if not future.done():
                c=self._connections.get(cid)
                if data.get("error"):
                    if c:c.failed_count+=1;c.last_error=str(data.get("error"))[:300]
                else:
                    if c:c.success_count+=1
                future.set_result(data)

    async def _send_auth_ack(self,conn:ExtensionConnection)->None:
        try:await conn.ws.send(json.dumps({"type":"auth_sync_ack","connectionId":conn.extension_connection_id or conn.id,"backendConnectionId":conn.id,"status":"synced" if conn.ready else "auth_syncing","email":conn.account_email,"credits":conn.credits,"busy":False,"activeSlots":0,"slotCapacity":conn.max_slots}))
        except Exception:pass

    async def refresh_account(self,connection_id:str)->None:
        conn=self.get(connection_id)
        if not conn or not conn.flow_key:return
        # Never let a stale balance route a new paid video to this account
        # while the current credits request is in flight or unavailable.
        conn.credits=None
        api_key=conn.flow_api_key or self.flow_api_key
        if not api_key:
            conn.last_error="flow_api_key_unavailable";await self._send_auth_ack(conn);return
        spec={"url":f"{FLOW_CREDITS_URL}?key={api_key}","method":"GET","headers":{"authorization":f"Bearer {conn.flow_key}","origin":"https://labs.google","referer":"https://labs.google/"},"responseType":"json","timeoutMs":30000}
        response=await self.send_rpc(connection_id,"SW_FETCH",{"spec":spec},timeout=35)
        if response.get("error"):
            self._invalidate_auth(conn,str(response["error"]));await self._send_auth_ack(conn);return
        inner=response.get("data") if isinstance(response,dict) else None
        if not isinstance(inner,dict) or inner.get("ok") is False:
            status=inner.get("status") if isinstance(inner,dict) else None
            reason=f"flow_credits_http_{status}" if isinstance(status,int) else "flow_credits_unavailable"
            if status in {401,403}:self._invalidate_auth(conn,reason)
            else:conn.last_error=reason
            await self._send_auth_ack(conn);return
        payload=inner.get("data")
        if isinstance(payload,dict):
            conn.last_error=None;conn.paygate_tier=resolve_paygate_tier(payload)
            conn.credits=payload.get("credits") if isinstance(payload.get("credits"),int) else None;conn.sku=payload.get("sku") if isinstance(payload.get("sku"),str) else None
        else:conn.last_error="flow_credits_invalid_response"
        await self._send_auth_ack(conn)

    async def send_rpc(self,connection_id:str,rpc_type:str,params:dict,*,timeout:float|None=None)->dict:
        conn=self.get(connection_id)
        if not conn:return {"error":"extension_disconnected"}
        import uuid
        req_id=str(uuid.uuid4());future=asyncio.get_running_loop().create_future();self._pending[req_id]=(future,connection_id);conn.request_count+=1
        try:
            await conn.ws.send(json.dumps({"id":req_id,"type":rpc_type,**params}));return await asyncio.wait_for(future,timeout=timeout or self.DEFAULT_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(req_id,None)
            try:await conn.ws.send(json.dumps({"type":"CANCEL_RPC","targetRequestId":req_id}))
            except Exception:pass
            conn.failed_count+=1;conn.last_error="timeout";return {"error":"timeout"}
        except asyncio.CancelledError:
            self._pending.pop(req_id,None)
            try:await conn.ws.send(json.dumps({"type":"CANCEL_RPC","targetRequestId":req_id}))
            except Exception:pass
            raise
        except Exception as exc:
            self._pending.pop(req_id,None);conn.failed_count+=1;conn.last_error=str(exc)[:300];return {"error":str(exc)}

    async def api_request(self,connection_id:str,*,url:str,method:str="POST",headers:dict|None=None,body:Any=None,captcha_action:str|None=None,timeout:float|None=None,response_type:str|None=None)->dict:
        conn=self.get(connection_id)
        if not conn:return {"error":"extension_disconnected"}
        bearer_resp=await self.send_rpc(connection_id,"GET_BEARER",{},timeout=self.BEARER_TIMEOUT);bearer=bearer_resp.get("data") if isinstance(bearer_resp,dict) else None
        if not isinstance(bearer,str) or not bearer:self._invalidate_auth(conn,bearer_resp.get("error") if isinstance(bearer_resp,dict) else None);return {"error":bearer_resp.get("error") or "no_bearer_from_extension"}
        conn.flow_key=bearer;final_body=copy.deepcopy(body)
        if captcha_action:
            tab_resp=await self.send_rpc(connection_id,"OPEN_FLOW_TAB",{"allowCreateHome":True},timeout=self.TAB_TIMEOUT);tab_data=tab_resp.get("data") if isinstance(tab_resp,dict) else None;tab_id=tab_data.get("tabId") if isinstance(tab_data,dict) else None
            if not tab_id:return {"error":f"flow_tab_open_failed:{tab_resp.get('error') or 'no_tab_id'}"}
            captcha_resp=await self.send_rpc(connection_id,"INJECT_RECAPTCHA",{"tabId":tab_id,"fallbackKey":RECAPTCHA_FALLBACK_KEY,"action":captcha_action},timeout=self.CAPTCHA_TIMEOUT);token=captcha_resp.get("data") if isinstance(captcha_resp,dict) else None
            if not isinstance(token,str) or not token:return {"error":f"captcha_failed:{captcha_resp.get('error') or 'empty_token'}"}
            if isinstance(final_body,dict):
                ctx=final_body.get("clientContext",{}).get("recaptchaContext")
                if isinstance(ctx,dict):ctx["token"]=token
                for item in final_body.get("requests") or []:
                    if isinstance(item,dict):
                        item_ctx=item.get("clientContext",{}).get("recaptchaContext")
                        if isinstance(item_ctx,dict):item_ctx["token"]=token
        fetch_headers=dict(headers or {});fetch_headers["authorization"]=f"Bearer {bearer}";is_get=method.upper() in {"GET","HEAD"}
        if is_get:fetch_headers={k:v for k,v in fetch_headers.items() if k.lower()!="content-type"}
        else:fetch_headers.setdefault("content-type","text/plain;charset=UTF-8")
        spec={"url":url,"method":method,"headers":fetch_headers,"timeoutMs":int((timeout or self.DEFAULT_TIMEOUT)*1000),"responseType":response_type or "json"}
        if final_body is not None and not is_get:spec["body"]=json.dumps(final_body)
        return self._normalize_fetch(await self.send_rpc(connection_id,"SW_FETCH",{"spec":spec},timeout=(timeout or self.DEFAULT_TIMEOUT)+5),"API")

    async def trpc_request(self,connection_id:str,*,url:str,method:str="GET",headers:dict|None=None,body:Any=None,timeout:float=30,response_type:str|None=None)->dict:
        conn=self.get(connection_id)
        if not conn:return {"error":"extension_disconnected"}
        bearer_resp=await self.send_rpc(connection_id,"GET_BEARER",{},timeout=self.BEARER_TIMEOUT);bearer=bearer_resp.get("data") if isinstance(bearer_resp,dict) else None
        if not bearer:self._invalidate_auth(conn,bearer_resp.get("error") if isinstance(bearer_resp,dict) else None);return {"error":"no_bearer_from_extension"}
        tab_resp=await self.send_rpc(connection_id,"OPEN_FLOW_TAB",{"allowCreateHome":False},timeout=self.TAB_TIMEOUT);tab_data=tab_resp.get("data") if isinstance(tab_resp,dict) else None;tab_id=tab_data.get("tabId") if isinstance(tab_data,dict) else None
        if not tab_id:return {"error":f"no_labs_tab:{tab_resp.get('error') or 'no_tab_id'}"}
        is_get=method.upper()=="GET";fetch_headers=dict(headers or {});fetch_headers["authorization"]=f"Bearer {bearer}"
        if not is_get:fetch_headers.setdefault("content-type","application/json")
        spec={"url":url,"method":method,"headers":fetch_headers,"timeoutMs":int(timeout*1000)}
        if response_type:spec["responseType"]=response_type
        if body is not None and not is_get:spec["body"]=json.dumps(body)
        return self._normalize_fetch(await self.send_rpc(connection_id,"INJECT_PAGE_FETCH",{"tabId":tab_id,"spec":spec},timeout=timeout+5),"TRPC",final_url_as_data=True)

    @staticmethod
    def _normalize_fetch(response:dict,prefix:str,final_url_as_data:bool=False)->dict:
        if response.get("error"):return {"error":response["error"]}
        inner=response.get("data") or {}
        if not isinstance(inner,dict):return {"error":f"unexpected_{prefix.lower()}_response"}
        out={"status":inner.get("status")}
        if isinstance(inner.get("headers"),dict):out["headers"]={str(k).lower():str(v) for k,v in inner["headers"].items()}
        if inner.get("data") is not None:out["data"]=inner["data"]
        elif isinstance(inner.get("text"),str):
            try:out["data"]=json.loads(inner["text"])
            except Exception:out["text"]=inner["text"]
        elif final_url_as_data and inner.get("finalUrl"):out["data"]={"url":inner["finalUrl"]}
        if inner.get("ok") is False:out["error"]=inner.get("error") or f"{prefix}_{inner.get('status','?')}"
        return out

    async def resolve_media_url(self,connection_id:str,media_id:str,*,thumbnail:bool=False)->str|None:
        query={"name":media_id}
        if thumbnail:query["mediaUrlType"]="MEDIA_URL_TYPE_THUMBNAIL"
        url=f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?{urlencode(query)}";response=await self.send_rpc(connection_id,"SW_FETCH",{"spec":{"url":url,"method":"GET","headers":{},"responseType":"none","timeoutMs":30000}},timeout=35);inner=response.get("data") if isinstance(response,dict) else None
        if not isinstance(inner,dict) or inner.get("ok") is False:return None
        status=inner.get("status")
        if isinstance(status,int) and status>=400:return None
        return inner.get("finalUrl") if isinstance(inner.get("finalUrl"),str) else None

    async def download_media(self,connection_id:str,media_id:str)->dict:
        """Download an image through a signed Flow media URL for account transfer."""
        url=await self.resolve_media_url(connection_id,media_id)
        if not url:return {"error":"media_url_unavailable"}
        try:
            parsed=urlparse(url)
        except Exception:
            return {"error":"media_url_invalid"}
        hostname=(parsed.hostname or "").lower()
        if parsed.scheme!="https" or not any(hostname==host or hostname.endswith(f".{host}") for host in MEDIA_DOWNLOAD_HOSTS):
            return {"error":"media_download_host_not_allowed"}
        try:
            async with httpx.AsyncClient(timeout=60,follow_redirects=False) as client:
                async with client.stream("GET",url) as response:
                    final_host=(response.url.host or "").lower()
                    if response.url.scheme!="https" or not any(final_host==host or final_host.endswith(f".{host}") for host in MEDIA_DOWNLOAD_HOSTS):
                        return {"error":"media_download_redirect_not_allowed"}
                    if response.status_code>=400:
                        return {"error":f"media_download_http_{response.status_code}","status":response.status_code}
                    content_length=response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length)>MAX_MEDIA_DOWNLOAD_BYTES:
                                return {"error":"media_download_too_large"}
                        except ValueError:
                            pass
                    chunks=[];total=0
                    async for chunk in response.aiter_bytes():
                        total+=len(chunk)
                        if total>MAX_MEDIA_DOWNLOAD_BYTES:
                            return {"error":"media_download_too_large"}
                        chunks.append(chunk)
                    mime_type=response.headers.get("content-type","").split(";",1)[0].strip().lower()
                    return {"status":response.status_code,"headers":dict(response.headers),"bytes":b"".join(chunks),"mime_type":mime_type}
        except httpx.HTTPError as exc:
            return {"error":f"media_download_failed:{type(exc).__name__}"}

    def mark_provider_failure(self,connection_id:str,error:str,*,status_code:int|None=None)->None:
        conn=self.get(connection_id)
        if not conn:return
        text=error.lower();conn.last_error=error[:300]
        if status_code in {401,403} or "401" in text or "403" in text or "unauth" in text:self._invalidate_auth(conn,error)
        if status_code==429 or "429" in text or "rate limit" in text or "quota" in text:
            conn.cooldown_until=time.time()+self.cooldown_seconds;conn.cooldown_reason="rate_limit" if "quota" not in text else "quota"


class BoundFlowClient:
    def __init__(self,bridge:FlowBridge,connection_id:str):
        self.bridge=bridge;self.connection_id=connection_id

    def _observe(self,result:dict)->dict:
        status=result.get("status") if isinstance(result,dict) else None
        if isinstance(result,dict) and (result.get("error") or (isinstance(status,int) and status>=400)):
            reason=str(result.get("error") or f"upstream_http_{status}")
            self.bridge.mark_provider_failure(
                self.connection_id,
                reason,
                status_code=status if isinstance(status,int) else None,
            )
        return result

    async def api_request(self,**kwargs):
        return self._observe(await self.bridge.api_request(self.connection_id,**kwargs))

    async def trpc_request(self,**kwargs):
        return self._observe(await self.bridge.trpc_request(self.connection_id,**kwargs))

    async def resolve_media_url(self,media_id:str,*,thumbnail:bool=False)->str|None:
        return await self.bridge.resolve_media_url(self.connection_id,media_id,thumbnail=thumbnail)

    async def download_media(self,media_id:str)->dict:
        return await self.bridge.download_media(self.connection_id,media_id)
