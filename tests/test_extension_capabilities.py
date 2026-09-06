from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.extension.gateway import REQUIRED_CAPABILITIES
from app.main import create_app


def _app():
    return create_app(Settings(env="test", bootstrap_api_key="test", project_store_path=":memory:"))


def _hello(**overrides):
    payload = {
        "type": "extension_ready",
        "protocolVersion": 7,
        "installationId": "install-capabilities",
        "extensionVersion": "1.0.12",
        "capabilities": sorted(REQUIRED_CAPABILITIES),
    }
    payload.update(overrides)
    return payload


def test_gateway_accepts_current_executor_capabilities():
    app = _app()
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            ws.send_json(_hello())
            assert app.state.runtime.bridge.connected is True


def test_gateway_rejects_an_incomplete_advertised_capability_set():
    app = _app()
    capabilities = sorted(REQUIRED_CAPABILITIES - {"rpc.cancel"})
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            ws.send_json(_hello(capabilities=capabilities))
            try:
                ws.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4406
            else:
                raise AssertionError("incomplete executor capabilities must close the socket")
        assert app.state.runtime.bridge.connected is False


def test_gateway_rejects_malformed_capabilities():
    app = _app()
    with TestClient(app) as client:
        with client.websocket_connect("/api/extensions/ws", subprotocols=["flow-provider-v7"]) as ws:
            ws.send_json(_hello(capabilities=["flow.session", 123]))
            try:
                ws.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4400
            else:
                raise AssertionError("malformed executor capabilities must close the socket")
        assert app.state.runtime.bridge.connected is False
