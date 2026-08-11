from __future__ import annotations

import time
from typing import Any


class ExtensionManager:
    """Runtime control plane for connected Chrome extension agents."""

    MANUAL_PAUSE_UNTIL=253402300799.0

    def __init__(self,bridge):
        self.bridge=bridge;self._history:dict[str,dict[str,Any]]={};self._paused:set[str]=set()

    def _snapshot(self,conn,*,connected:bool=True)->dict[str,Any]:
        now=time.time();paused=conn.installation_id in self._paused or conn.cooldown_reason=="manual_pause"
        cooldown=max(0,int(conn.cooldown_until-now)) if conn.cooldown_until and conn.cooldown_until>now and not paused else 0
        health_status=conn.health_status if connected else "offline"
        return {"id":conn.id,"installation_id":conn.installation_id,"runtime_id":conn.runtime_id,"profile_id":conn.profile_id,"profile_name":conn.profile_name,"email":conn.account_email,"connected":connected,"ready":bool(conn.ready and not paused),"paused":paused,"health_status":health_status,"suspect_since":conn.suspect_since,"paygate_tier":conn.paygate_tier,"sku":conn.sku,"credits":conn.credits,"slot_capacity":conn.max_slots,"cooldown_remaining_s":cooldown,"cooldown_reason":conn.cooldown_reason,"connected_at":conn.connected_at,"last_seen_at":conn.last_seen_at,"request_count":conn.request_count,"success_count":conn.success_count,"failed_count":conn.failed_count,"last_error":conn.last_error,"extension_connection_id":conn.extension_connection_id}

    def connected(self,conn)->None:
        if conn.installation_id in self._paused:
            conn.cooldown_until=self.MANUAL_PAUSE_UNTIL;conn.cooldown_reason="manual_pause"
        self._history[conn.installation_id]=self._snapshot(conn)

    def heartbeat(self,conn)->None:self._history[conn.installation_id]=self._snapshot(conn)

    def suspect(self,conn)->None:
        snap=self._snapshot(conn);snap["ready"]=False;snap["health_status"]="suspect";self._history[conn.installation_id]=snap

    def disconnected(self,conn)->None:
        current=self._current(conn.installation_id)
        if current is not None and current is not conn:return
        snap=self._snapshot(conn,connected=False);snap["ready"]=False;snap["health_status"]="offline";snap["disconnected_at"]=time.time();self._history[conn.installation_id]=snap

    def _current(self,installation_id:str):return self.bridge.get_connection_by_installation(installation_id)

    def get(self,installation_id:str)->dict[str,Any]|None:
        conn=self._current(installation_id)
        if conn:
            snap=self._snapshot(conn);self._history[installation_id]=snap;return snap
        snap=self._history.get(installation_id);return dict(snap) if snap else None

    def list(self)->list[dict[str,Any]]:
        for conn in self.bridge.connections():self._history[conn.installation_id]=self._snapshot(conn)
        return sorted((dict(v) for v in self._history.values()),key=lambda item:item.get("last_seen_at") or 0,reverse=True)

    def pause(self,installation_id:str)->dict[str,Any]|None:
        if not self.get(installation_id):return None
        self._paused.add(installation_id);conn=self._current(installation_id)
        if conn:
            conn.cooldown_until=self.MANUAL_PAUSE_UNTIL;conn.cooldown_reason="manual_pause";self._history[installation_id]=self._snapshot(conn)
        else:
            self._history[installation_id]["paused"]=True;self._history[installation_id]["ready"]=False;self._history[installation_id]["cooldown_reason"]="manual_pause"
        return self.get(installation_id)

    def resume(self,installation_id:str)->dict[str,Any]|None:
        if not self.get(installation_id):return None
        self._paused.discard(installation_id);conn=self._current(installation_id)
        if conn and conn.cooldown_reason=="manual_pause":
            conn.cooldown_until=None;conn.cooldown_reason=None;self._history[installation_id]=self._snapshot(conn)
        else:
            self._history[installation_id]["paused"]=False
            if self._history[installation_id].get("cooldown_reason")=="manual_pause":self._history[installation_id]["cooldown_reason"]=None
        return self.get(installation_id)

    async def ping(self,installation_id:str)->dict[str,Any]:
        conn=self._current(installation_id)
        if not conn:return {"error":"extension_offline"}
        started=time.monotonic();response=await self.bridge.send_rpc(conn.id,"PING",{},timeout=10);latency_ms=round((time.monotonic()-started)*1000,2)
        if response.get("error"):return {"error":response["error"],"latency_ms":latency_ms}
        self.bridge.mark_healthy(conn.id);self.heartbeat(conn);return {"ok":True,"latency_ms":latency_ms,"extension":response.get("data")}

    async def refresh_auth(self,installation_id:str)->dict[str,Any]:
        conn=self._current(installation_id)
        if not conn:return {"error":"extension_offline"}
        response=await self.bridge.send_rpc(conn.id,"GET_BEARER",{"force":True},timeout=self.bridge.BEARER_TIMEOUT);token=response.get("data") if isinstance(response,dict) else None
        if not isinstance(token,str) or not token:
            self.bridge.invalidate_auth(conn.id,response.get("error") if isinstance(response,dict) else "auth_refresh_failed");await self.bridge.send_auth_ack(conn.id);return {"error":response.get("error") or "auth_refresh_failed"}
        conn.flow_key=token;await self.bridge.refresh_account(conn.id);self._history[installation_id]=self._snapshot(conn);return {"ok":True,"extension":self.get(installation_id)}

    async def open_flow(self,installation_id:str)->dict[str,Any]:
        conn=self._current(installation_id)
        if not conn:return {"error":"extension_offline"}
        response=await self.bridge.send_rpc(conn.id,"OPEN_FLOW_TAB",{"allowCreateHome":True},timeout=self.bridge.TAB_TIMEOUT)
        if response.get("error"):return {"error":response["error"]}
        return {"ok":True,"tab":response.get("data")}

    async def reconnect(self,installation_id:str)->dict[str,Any]:
        conn=self._current(installation_id)
        if not conn:return {"error":"extension_offline"}
        try:await conn.ws.close(4001,"provider_admin_reconnect")
        except Exception as exc:return {"error":str(exc)}
        return {"ok":True,"status":"reconnect_requested"}

    async def diagnostics(self,installation_id:str)->dict[str,Any]|None:
        snapshot=self.get(installation_id)
        if not snapshot:return None
        conn=self._current(installation_id);ping=await self.ping(installation_id) if conn else {"error":"extension_offline"}
        return {"extension":self.get(installation_id),"ping":ping,"pending_rpc":self.bridge.pending_count(conn.id) if conn else 0,"known_extensions":len(self._history),"connected_extensions":self.bridge.connected_count()}
