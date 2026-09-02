import asyncio
import httpx
import app.providers.google_flow.client as flow_client_module

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.providers.google_flow.browser_bridge import FlowBridge
from app.providers.google_flow.client import BoundFlowClient


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


async def test_auth_available_clears_previous_google_account_state(monkeypatch):
    bridge = FlowBridge(flow_api_key="test-key")
    socket = FakeSocket()
    connection = bridge.register(socket, {
        "installationId": "install-test", "runtimeId": "chrome", "profileId": "profile",
    })
    connection.account_email = "old@example.com"
    connection.paygate_tier = "PAYGATE_TIER_ONE"
    connection.credits = 100

    async def no_refresh(_connection_id):
        return None

    monkeypatch.setattr(bridge, "refresh_account", no_refresh)
    await bridge.handle_message({"type": "auth_available"}, socket)

    assert connection.account_email is None
    assert connection.paygate_tier is None
    assert connection.credits is None
    assert not connection.ready
    await bridge.close_background_tasks()


async def test_account_refresh_ignores_delayed_previous_account_after_switch(monkeypatch):
    bridge = FlowBridge(flow_api_key="test-key")
    socket = FakeSocket()
    connection = bridge.register(socket, {"installationId": "install-switch", "profileId": "profile"})
    connection.flow_key = "browser_owned"
    connection.account_email = "old@example.com"
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def fake_rpc(_connection_id, _rpc_type, _params, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            try:
                await release_first.wait()
            except asyncio.CancelledError:
                # Simulate a response that was already accepted by Flow when
                # the account switch arrived.
                await release_first.wait()
            return {"data": {"ok": True, "status": 200, "data": {
                "userPaygateTier": "PAYGATE_TIER_ONE", "credits": 111, "sku": "old",
            }}}
        return {"data": {"ok": True, "status": 200, "data": {
            "userPaygateTier": "PAYGATE_TIER_ONE", "credits": 222, "sku": "new",
        }}}

    monkeypatch.setattr(bridge, "send_rpc", fake_rpc)
    bridge.schedule_account_refresh(connection.id)
    await first_started.wait()

    switched = asyncio.create_task(bridge.handle_message({"type": "auth_available"}, socket))
    await asyncio.sleep(0)
    release_first.set()
    await switched
    await bridge.handle_message({"type": "user_info", "userInfo": {"email": "new@example.com"}}, socket)

    for _ in range(20):
        if calls >= 2 and connection.credits == 222:
            break
        await asyncio.sleep(0)

    assert calls == 2
    assert connection.account_email == "new@example.com"
    assert connection.credits == 222
    assert connection.sku == "new"
    assert connection.auth_generation == 1
    await bridge.close_background_tasks()


async def test_extension_simulation_mode_is_tracked_without_reconnecting():
    bridge = FlowBridge(flow_api_key="test-key")
    socket = FakeSocket()
    connection = bridge.register(socket, {
        "installationId": "install-test", "simulationMode": True,
    })

    assert connection.simulation_mode is True
    await bridge.handle_message({"type": "simulation_mode_changed", "simulationMode": False}, socket)
    assert connection.simulation_mode is False


async def test_media_redirect_uses_browser_cookies_and_url_encodes_id(monkeypatch):
    bridge = FlowBridge(flow_api_key="test-key")
    captured = {}

    async def fake_rpc(connection_id, rpc_type, params, **kwargs):
        captured.update({
            "connection_id": connection_id,
            "rpc_type": rpc_type,
            "params": params,
            "kwargs": kwargs,
        })
        return {
            "data": {
                "ok": True,
                "status": 200,
                "finalUrl": "https://flow-content.google/video/signed",
            },
        }

    monkeypatch.setattr(bridge, "send_rpc", fake_rpc)
    url = await bridge.resolve_media_url("account-1", "media/id with spaces")

    assert url == "https://flow-content.google/video/signed"
    assert captured["rpc_type"] == "SW_FETCH"
    assert "name=media%2Fid+with+spaces" in captured["params"]["spec"]["url"]
    assert captured["params"]["spec"]["responseType"] == "none"


async def test_thumbnail_redirect_requests_thumbnail_media_url(monkeypatch):
    bridge = FlowBridge(flow_api_key="test-key")
    captured = {}

    async def fake_rpc(_connection_id, _rpc_type, params, **_kwargs):
        captured.update(params["spec"])
        return {
            "data": {
                "ok": True,
                "status": 200,
                "finalUrl": "https://flow-content.google/thumbnail/signed",
            },
        }

    monkeypatch.setattr(bridge, "send_rpc", fake_rpc)
    url = await bridge.resolve_media_url("account-1", "media/video-1", thumbnail=True)

    assert url == "https://flow-content.google/thumbnail/signed"
    assert "name=media%2Fvideo-1" in captured["url"]
    assert "mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL" in captured["url"]


async def test_download_media_reads_only_an_allowed_signed_image_url(monkeypatch):
    bridge = FlowBridge(flow_api_key="test-key")

    async def fake_resolve(_connection_id, _media_id, **_kwargs):
        return "https://flow-content.google/image/signed"

    class FakeResponse:
        url = httpx.URL("https://flow-content.google/image/signed")
        status_code = 200
        headers = {"content-type": "image/png", "content-length": "5"}

        def aiter_bytes(self):
            async def chunks():
                yield b"hello"

            return chunks()

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return False

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url):
            assert method == "GET"
            assert url == "https://flow-content.google/image/signed"
            return FakeStream()

    monkeypatch.setattr(bridge, "resolve_media_url", fake_resolve)
    monkeypatch.setattr(flow_client_module.httpx, "AsyncClient", FakeClient)

    result = await bridge.download_media("account-1", "media/image")

    assert result["bytes"] == b"hello"
    assert result["mime_type"] == "image/png"


async def test_bound_client_applies_rate_limit_cooldown_and_auth_invalidation(monkeypatch):
    bridge = FlowBridge(flow_api_key="test-key", cooldown_seconds=180)
    socket = FakeSocket()
    connection = bridge.register(socket, {"installationId": "install-test"})
    connection.flow_key = "browser-owned"
    connection.account_email = "owner@example.com"
    connection.paygate_tier = "PAYGATE_TIER_ONE"
    responses = [
        {"status": 429, "error": "upstream_http_429"},
        {"status": 401, "error": "upstream_http_401"},
    ]

    async def fake_api_request(_connection_id, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(bridge, "api_request", fake_api_request)
    client = BoundFlowClient(bridge, connection.id)
    await client.api_request(url="https://example.test")
    assert connection.cooldown_until is not None
    assert connection.cooldown_reason == "rate_limit"

    await client.api_request(url="https://example.test")
    assert connection.flow_key is None
    assert connection.paygate_tier is None


def test_extension_connects_on_gateway_runtime_path():
    app = create_app(Settings(env="test", bootstrap_api_key="test", project_store_path=":memory:"))
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            assert ws.accepted_subprotocol == "flow-provider-v7"
            ws.send_json({"type": "extension_ready", "protocolVersion": 7,
                "installationId": "install-test", "profileName": "Test Chrome"})
            assert client.get("/api/health").json()["ok"] is True
            assert app.state.runtime.bridge.connected is True
        assert app.state.runtime.bridge.connected is False


def test_extension_gateway_rejects_an_invalid_connector_key():
    app = create_app(Settings(
        env="test",
        bootstrap_api_key="test",
        extension_api_key="connector-secret",
        project_store_path=":memory:",
    ))
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            ws.send_json({
                "type": "extension_ready",
                "protocolVersion": 7,
                "installationId": "attacker",
                "connectorApiKey": "wrong-secret",
                "simulationMode": True,
            })
            try:
                ws.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4401
            else:
                raise AssertionError("invalid connector key must close the socket")
        assert app.state.runtime.bridge.connected is False


def test_extension_gateway_requires_the_versioned_subprotocol():
    app = create_app(Settings(env="test", bootstrap_api_key="test", project_store_path=":memory:"))
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws") as ws:
            try:
                ws.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4406
            else:
                raise AssertionError("missing extension subprotocol must close the socket")


def test_extension_gateway_rejects_simulation_when_disabled():
    app = create_app(Settings(
        env="test",
        bootstrap_api_key="test",
        extension_api_key="connector-secret",
        allow_simulation_mode=False,
        project_store_path=":memory:",
    ))
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            ws.send_json({
                "type": "extension_ready",
                "protocolVersion": 7,
                "installationId": "connector",
                "connectorApiKey": "connector-secret",
                "simulationMode": True,
            })
            try:
                ws.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4403
            else:
                raise AssertionError("disabled simulation mode must close the socket")
        assert app.state.runtime.bridge.connected is False


def test_legacy_extension_websocket_is_removed():
    app = create_app(Settings(env="test", bootstrap_api_key="test", project_store_path=":memory:"))
    paths = {
        route.path
        for included in app.routes
        for route in getattr(getattr(included, "original_router", None), "routes", [])
    }
    assert "/api/extensions/ws" in paths
    assert "/v1/extensions/ws" not in paths
