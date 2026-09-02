from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_uses_browser_owned_bridge():
    source = (ROOT / "app/runtime.py").read_text(encoding="utf-8")
    assert "from app.providers.google_flow.browser_bridge import FlowBridge" in source


def test_backend_never_receives_google_bearer():
    source = (ROOT / "app/providers/google_flow/browser_bridge.py").read_text(encoding="utf-8")
    api = source.split("async def api_request", 1)[1].split("async def trpc_request", 1)[0]
    trpc = source.split("async def trpc_request", 1)[1]
    assert "GET_BEARER" not in api and "GET_BEARER" not in trpc
    assert '"authMode": "flow"' in api and '"authMode": "flow"' in trpc


def test_extension_injects_authorization_inside_browser():
    source = (ROOT / "extension/browser-transport.js").read_text(encoding="utf-8")
    assert "const token = await getBearer({ expectedGeneration: generation });" in source
    assert "authorization: `Bearer ${token}`" in source
    assert 'type: "auth_available"' in source
    assert 'type: "token_captured"' not in source
