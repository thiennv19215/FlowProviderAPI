from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_uses_browser_owned_bridge():
    source = (ROOT / "app/runtime.py").read_text(encoding="utf-8")
    assert "from app.providers.google_flow.browser_bridge import FlowBridge" in source


def test_backend_flow_requests_do_not_fetch_bearer_first():
    source = (ROOT / "app/providers/google_flow/browser_bridge.py").read_text(encoding="utf-8")
    api = source.split("async def api_request", 1)[1].split("async def trpc_request", 1)[0]
    trpc = source.split("async def trpc_request", 1)[1]
    assert "GET_BEARER" not in api
    assert "GET_BEARER" not in trpc
    assert '"authMode": "flow"' in api
    assert '"authMode": "flow"' in trpc


def test_extension_injects_flow_authorization_inside_browser():
    source = (ROOT / "extension/browser-transport.js").read_text(encoding="utf-8")
    assert "const token = await getBearer();" in source
    assert "authorization: `Bearer ${token}`" in source
    assert 'msg?.spec?.authMode === FLOW_AUTH_MODE' in source


def test_auth_sync_never_sends_bearer_to_backend():
    source = (ROOT / "extension/browser-transport.js").read_text(encoding="utf-8")
    assert 'type: "auth_available"' in source
    assert 'type: "token_captured"' not in source
    assert "flowKey:" not in source


def test_extension_discovers_and_sends_flow_api_key_only_from_google_host():
    source = (ROOT / "extension/browser-transport.js").read_text(encoding="utf-8")
    assert 'const FLOW_API_HOST = "aisandbox-pa.googleapis.com"' in source
    assert 'type: "flow_api_key"' in source
    assert 'url.hostname !== FLOW_API_HOST' in source
    assert "chrome.webRequest.onBeforeRequest.addListener" in source


def test_dispatch_disconnect_is_not_blindly_retried():
    source = (ROOT / "app/jobs/worker.py").read_text(encoding="utf-8")
    repository_source = (ROOT / "app/jobs/repository.py").read_text(encoding="utf-8")
    assert 'safe_to_retry=job.stage in {"preparing"}' in source
    assert 'INTERRUPTED_DURING_DISPATCH' in repository_source
    assert 'request was not replayed to avoid duplicate generation' in repository_source
