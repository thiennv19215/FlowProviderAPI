from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.google_flow.browser_bridge import FlowBridge


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


def test_legacy_extension_websocket_is_removed():
    app = create_app(Settings(env="test", bootstrap_api_key="test", project_store_path=":memory:"))
    paths = {
        route.path
        for included in app.routes
        for route in getattr(getattr(included, "original_router", None), "routes", [])
    }
    assert "/api/extensions/ws" in paths
    assert "/v1/extensions/ws" not in paths
