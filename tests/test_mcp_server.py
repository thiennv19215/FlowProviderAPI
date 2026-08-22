import base64
import json
from pathlib import Path

import httpx
from mcp import Client

import app.mcp_server as mcp_server_module
from app.mcp_server import FlowProviderClient, MCPSettings, build_mcp_server


def mock_client(handler, *, allowed_roots: str = "."):
    settings = MCPSettings(
        base_url="https://provider.test",
        api_key="fpa_test",
        timeout_seconds=10,
        allowed_roots=allowed_roots,
    )
    return FlowProviderClient(settings, transport=httpx.MockTransport(handler))


async def test_mcp_lists_business_tools():
    server = build_mcp_server(mock_client(lambda _request: httpx.Response(200, json={})))
    async with Client(server) as client:
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == {
        "flow_check_health",
        "flow_list_projects",
        "flow_create_project",
        "flow_upload_image",
        "flow_generate_image",
        "flow_generate_video",
        "flow_get_video_status",
    }


async def test_generate_image_encodes_local_reference_and_maps_agent_values():
    image_path = Path("reference-generated-c1996c19.png").resolve()
    image_bytes = image_path.read_bytes()
    captured = {}

    def handler(request: httpx.Request):
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"media": [{"name": "media/generated"}]},
            headers={
                "X-Request-Id": "req_mcp",
                "X-Flow-Project-Id": "projects/managed",
            },
        )

    server = build_mcp_server(mock_client(handler))
    async with Client(server) as client:
        result = await client.call_tool(
            "flow_generate_image",
            {
                "prompt": "a quiet mountain lake",
                "model": "v2",
                "aspect_ratio": "16:9",
                "variant_count": 2,
                "image_paths": [str(image_path)],
            },
        )

    assert result.is_error is False
    assert captured["request"].headers["authorization"] == "Bearer fpa_test"
    assert captured["request"].url.path == "/v1/images/generations"
    assert captured["body"]["model"] == "NANO_BANANA_2"
    assert captured["body"]["aspect_ratio"] == "IMAGE_ASPECT_RATIO_LANDSCAPE"
    assert captured["body"]["variant_count"] == 2
    assert captured["body"]["input_images"] == [{
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        "mime_type": "image/png",
        "file_name": image_path.name,
    }]
    assert result.structured_content["metadata"]["x-flow-project-id"] == "projects/managed"


async def test_provider_error_is_returned_as_model_visible_tool_error():
    def handler(_request: httpx.Request):
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "PROVIDER_ACCOUNT_UNAVAILABLE",
                    "message": "No browser account is ready.",
                    "retryable": True,
                    "request_id": "req_unavailable",
                    "details": [{
                        "field": "provider_account",
                        "code": "NOT_READY",
                        "message": "Connect a browser account.",
                    }],
                }
            },
        )

    server = build_mcp_server(mock_client(handler))
    async with Client(server) as client:
        result = await client.call_tool("flow_check_health")

    assert result.is_error is True
    assert "PROVIDER_ACCOUNT_UNAVAILABLE" in result.content[0].text
    assert "retryable=true" in result.content[0].text
    assert "req_unavailable" in result.content[0].text
    assert "provider_account NOT_READY: Connect a browser account." in result.content[0].text


async def test_video_tool_validates_type_specific_fields_before_http_call():
    called = False

    def handler(_request: httpx.Request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    server = build_mcp_server(mock_client(handler))
    async with Client(server) as client:
        result = await client.call_tool(
            "flow_generate_video",
            {"type": "image_to_video", "prompt": "move slowly"},
        )

    assert result.is_error is True
    assert "start_media_id is required" in result.content[0].text
    assert called is False


async def test_video_tool_uses_type_specific_default_aspect_ratios():
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"operations": []})

    server = build_mcp_server(mock_client(handler))
    async with Client(server) as client:
        image_to_video = await client.call_tool(
            "flow_generate_video",
            {
                "type": "image_to_video",
                "prompt": "move slowly",
                "start_media_id": "media/start",
            },
        )
        omni = await client.call_tool(
            "flow_generate_video",
            {
                "type": "omni",
                "prompt": "combine references",
                "reference_media_ids": ["media/reference"],
            },
        )

    assert image_to_video.is_error is False
    assert omni.is_error is False
    assert bodies[0]["aspect_ratio"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"
    assert bodies[1]["aspect_ratio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"


async def test_image_tools_reject_paths_outside_allowed_roots():
    called = False

    def handler(_request: httpx.Request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    allowed_root = Path("tests").resolve()
    outside_image = Path("reference-generated-c1996c19.png").resolve()
    server = build_mcp_server(mock_client(handler, allowed_roots=str(allowed_root)))
    async with Client(server) as client:
        result = await client.call_tool(
            "flow_upload_image",
            {"image_path": str(outside_image)},
        )

    assert result.is_error is True
    assert "outside FLOW_PROVIDER_MCP_ALLOWED_ROOTS" in result.content[0].text
    assert called is False


async def test_image_generation_preflights_combined_base64_limit(monkeypatch):
    called = False

    def handler(_request: httpx.Request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    monkeypatch.setattr(mcp_server_module, "MAX_BASE64_TOTAL_CHARS", 1)
    image_path = Path("reference-generated-c1996c19.png").resolve()
    server = build_mcp_server(mock_client(handler))
    async with Client(server) as client:
        result = await client.call_tool(
            "flow_generate_image",
            {"prompt": "test", "image_paths": [str(image_path)]},
        )

    assert result.is_error is True
    assert "Base64 request limit" in result.content[0].text
    assert called is False
