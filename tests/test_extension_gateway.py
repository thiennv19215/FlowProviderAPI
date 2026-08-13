from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_extension_connects_on_gateway_runtime_path():
    app = create_app(Settings(env="test", bootstrap_api_key="test"))
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            assert ws.accepted_subprotocol == "flow-provider-v7"
            ws.send_json({"type": "extension_ready", "protocolVersion": 7,
                "installationId": "install-test", "profileName": "Test Chrome"})
            assert client.get("/api/health").json()["ok"] is True
            assert app.state.runtime.bridge.connected is True
        assert app.state.runtime.bridge.connected is False


def test_legacy_extension_websocket_is_removed():
    app = create_app(Settings(env="test", bootstrap_api_key="test"))
    paths = {
        route.path
        for included in app.routes
        for route in getattr(getattr(included, "original_router", None), "routes", [])
    }
    assert "/api/extensions/ws" in paths
    assert "/v1/extensions/ws" not in paths
