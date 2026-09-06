import httpx
import pytest

from app.mcp_server import FlowProviderClient, MCPSettings


@pytest.mark.asyncio
async def test_mcp_client_sends_configured_bearer_api_key(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ok"})

    settings = MCPSettings(
        base_url="https://provider.test",
        api_key="fpa_prod_business_secret",
        allowed_roots=str(tmp_path),
    )
    client = FlowProviderClient(settings, transport=httpx.MockTransport(handler))
    try:
        result = await client.request("GET", "/health/live")
    finally:
        await client.close()

    assert result.status_code == 200
    assert seen["authorization"] == "Bearer fpa_prod_business_secret"


@pytest.mark.asyncio
async def test_mcp_client_keeps_local_no_auth_compatibility(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ok"})

    settings = MCPSettings(
        base_url="http://localhost:8000",
        api_key=None,
        allowed_roots=str(tmp_path),
    )
    client = FlowProviderClient(settings, transport=httpx.MockTransport(handler))
    try:
        result = await client.request("GET", "/health/live")
    finally:
        await client.close()

    assert result.status_code == 200
    assert seen["authorization"] is None


def test_mcp_api_key_can_reuse_bootstrap_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_PROVIDER_BOOTSTRAP_API_KEY", "fpa_prod_from_env")
    settings = MCPSettings(
        base_url="https://provider.test",
        allowed_roots=str(tmp_path),
        _env_file=None,
    )
    assert settings.api_key == "fpa_prod_from_env"
