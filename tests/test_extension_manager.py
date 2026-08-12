import asyncio
import json


class LoopbackExtension:
    def __init__(self, bridge):
        self.bridge=bridge
        self.closed=[]

    async def send(self, payload: str):
        msg=json.loads(payload)
        req_id=msg.get("id")
        if not req_id:return
        if msg["type"]=="PING":data={"version":"1.0.0-test"}
        elif msg["type"]=="GET_BEARER":data="mock-bearer-refreshed"
        elif msg["type"]=="SW_FETCH":data={"ok":True,"status":200,"data":{"userPaygateTier":"PAYGATE_TIER_ONE","credits":777,"sku":"MOCK_PRO"}}
        elif msg["type"]=="OPEN_FLOW_TAB":data={"tabId":42,"isNew":False}
        else:
            await self.bridge.handle_message({"id":req_id,"error":f"unsupported:{msg['type']}"},self);return
        await self.bridge.handle_message({"id":req_id,"data":data},self)

    async def close(self, code=1000, reason=""):
        self.closed.append((code,reason))


def _register(app):
    bridge=app.state.runtime.bridge
    ws=LoopbackExtension(bridge)
    conn=bridge.register(ws,{"installationId":"install_manage_1","runtimeId":"chrome","profileId":"profile_manage_1","profileName":"Managed Chrome","connectionId":"conn_manage_1"})
    conn.account_email="managed@example.test";conn.flow_key="initial-token";conn.flow_api_key="AIzaMockFlowApiKey1234567890";conn.paygate_tier="PAYGATE_TIER_ONE";conn.credits=100
    app.state.runtime.extension_manager.connected(conn)
    return ws,conn


def test_extension_registry_pause_resume_and_api(client,app,auth):
    _ws,conn=_register(app)
    listed=client.get("/v1/extensions",headers=auth)
    assert listed.status_code==200
    assert listed.json()["data"][0]["installation_id"]=="install_manage_1"
    assert listed.json()["data"][0]["connected"] is True

    paused=client.post("/v1/extensions/install_manage_1/pause",headers=auth)
    assert paused.status_code==200
    assert paused.json()["paused"] is True
    assert conn not in app.state.runtime.bridge.ready_connections()

    resumed=client.post("/v1/extensions/install_manage_1/resume",headers=auth)
    assert resumed.status_code==200
    assert resumed.json()["paused"] is False
    assert conn in app.state.runtime.bridge.ready_connections()


def test_extension_control_rpc_and_offline_history(client,app,auth):
    ws,conn=_register(app)

    ping=client.post("/v1/extensions/install_manage_1/ping",headers=auth)
    assert ping.status_code==200
    assert ping.json()["extension"]["version"]=="1.0.0-test"

    refreshed=client.post("/v1/extensions/install_manage_1/refresh-auth",headers=auth)
    assert refreshed.status_code==200
    assert conn.credits==777
    assert conn.sku=="MOCK_PRO"

    opened=client.post("/v1/extensions/install_manage_1/open-flow",headers=auth)
    assert opened.status_code==200
    assert opened.json()["tab"]["tabId"]==42

    diagnostics=client.get("/v1/extensions/install_manage_1/diagnostics",headers=auth)
    assert diagnostics.status_code==200
    assert diagnostics.json()["extension"]["connected"] is True
    assert diagnostics.json()["pending_rpc"]==0

    reconnect=client.post("/v1/extensions/install_manage_1/reconnect",headers=auth)
    assert reconnect.status_code==200
    assert ws.closed[-1][0]==4001

    app.state.runtime.extension_manager.disconnected(conn)
    app.state.runtime.bridge.clear(connection_id=conn.id)
    offline=client.get("/v1/extensions/install_manage_1",headers=auth)
    assert offline.status_code==200
    assert offline.json()["connected"] is False
    assert offline.json()["ready"] is False
    assert client.post("/v1/extensions/install_manage_1/ping",headers=auth).status_code==409


def test_unknown_extension_returns_structured_404(client,auth):
    response=client.get("/v1/extensions/does-not-exist",headers=auth)
    assert response.status_code==404
    assert response.json()["error"]["code"]=="EXTENSION_NOT_FOUND"
