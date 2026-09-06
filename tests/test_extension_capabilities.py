import json
import time

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.extension.gateway import REQUIRED_CAPABILITIES, _hello_diagnostics
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
            deadline = time.monotonic() + 1.0
            while not app.state.runtime.bridge.connected and time.monotonic() < deadline:
                time.sleep(0.01)
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


def test_handshake_diagnostics_never_include_credentials_or_identity():
    hello = _hello(
        connectorApiKey="fpe_prod_super_secret",
        installationId="install-private-identifier",
        account_email="private@example.com",
        userInfo={"email": "private@example.com"},
    )
    diagnostics = _hello_diagnostics(hello)
    assert diagnostics == {
        "protocol": 7,
        "extension_version": "1.0.12",
        "capability_count": len(REQUIRED_CAPABILITIES),
    }
    encoded = json.dumps(diagnostics)
    assert "super_secret" not in encoded
    assert "private@example.com" not in encoded
    assert "install-private-identifier" not in encoded
