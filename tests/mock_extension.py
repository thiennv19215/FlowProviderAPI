from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class MockFlowState:
    media_base_url: str
    credits: int = 500
    tier: str = "PAYGATE_TIER_ONE"
    sku: str = "mock-flow-pro"
    projects_created: int = 0
    uploads: int = 0
    image_generations: int = 0
    video_generations: int = 0
    omni_generations: int = 0
    polls: int = 0
    rpc_types: list[str] = field(default_factory=list)


class MockExtensionSocket:
    """Loopback implementation of the Chrome extension RPC contract.

    The backend still executes FlowBridge, FlowSDK, GoogleFlowProvider,
    scheduler, durable jobs and asset ingestion. Only the browser/Google side
    is replaced by deterministic responses at the WebSocket RPC boundary.
    """

    def __init__(self, bridge, *, media_base_url: str):
        self.bridge = bridge
        self.state = MockFlowState(media_base_url=media_base_url.rstrip("/"))
        self.connection = None
        self.closed = False

    async def connect(self):
        hello = {
            "type": "extension_ready",
            "protocolVersion": 7,
            "installationId": "mock-installation-0001",
            "runtimeId": "chrome",
            "profileId": "mock-profile-1",
            "profileName": "Mock Chrome Profile",
            "connectionId": "mock-connection-1",
        }
        self.connection = self.bridge.register(self, hello)
        await self.bridge.handle_message(hello, self)
        await self.bridge.handle_message(
            {
                "type": "user_info",
                "userInfo": {
                    "email": "mock-flow@example.com",
                    "name": "Mock Flow Account",
                    "verified_email": True,
                },
            },
            self,
        )
        await self.bridge.handle_message(
            {"type": "token_captured", "flowKey": "mock-bearer-token"}, self
        )
        for _ in range(30):
            if self.connection.ready and self.connection.credits is not None:
                break
            await asyncio.sleep(0)
        assert self.connection.ready
        return self.connection

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True
        if self.connection:
            self.bridge.clear(connection_id=self.connection.id)

    async def send(self, payload: str):
        message = json.loads(payload)
        request_id = message.get("id")
        rpc_type = message.get("type")
        if not request_id:
            return
        self.state.rpc_types.append(str(rpc_type))
        response = self._response_for(message)
        response["id"] = request_id
        asyncio.create_task(self.bridge.handle_message(response, self))

    def _response_for(self, message: dict) -> dict:
        rpc_type = message.get("type")
        if rpc_type == "GET_BEARER":
            return {"data": "mock-bearer-token"}
        if rpc_type in {"OPEN_FLOW_TAB", "ENSURE_TAB"}:
            return {"data": {"tabId": 101, "isNew": False}}
        if rpc_type == "RELOAD_TAB":
            return {"data": {"ok": True}}
        if rpc_type == "INJECT_RECAPTCHA":
            return {"data": "mock-recaptcha-token"}
        if rpc_type == "INJECT_PAGE_FETCH":
            return {"data": self._page_fetch(message.get("spec") or {})}
        if rpc_type == "SW_FETCH":
            return {"data": self._sw_fetch(message.get("spec") or {})}
        return {"error": f"mock_unsupported_rpc:{rpc_type}"}

    def _page_fetch(self, spec: dict) -> dict:
        url = str(spec.get("url") or "")
        if "project.createProject" in url:
            self.state.projects_created += 1
            project_id = f"mock-project-{self.state.projects_created}"
            return {
                "ok": True,
                "status": 200,
                "data": {
                    "result": {
                        "data": {"json": {"result": {"projectId": project_id}}}
                    }
                },
            }
        if "media.getMediaUrlRedirect" in url:
            media_id = urlparse(url).query.split("name=", 1)[-1]
            return {
                "ok": True,
                "status": 200,
                "finalUrl": f"{self.state.media_base_url}/media/{media_id}",
            }
        return {"ok": True, "status": 200, "data": {}}

    def _sw_fetch(self, spec: dict) -> dict:
        url = str(spec.get("url") or "")
        body = self._body(spec)

        if "/v1/credits" in url:
            return {
                "ok": True,
                "status": 200,
                "data": {
                    "userPaygateTier": self.state.tier,
                    "credits": self.state.credits,
                    "sku": self.state.sku,
                },
            }

        if "/v1/flow/uploadImage" in url:
            self.state.uploads += 1
            return {
                "ok": True,
                "status": 200,
                "data": {"media": {"name": f"mock-upload-{self.state.uploads}"}},
            }

        if "flowMedia:batchGenerateImages" in url:
            self.state.image_generations += 1
            count = max(1, len(body.get("requests") or []))
            items = []
            for index in range(count):
                media_id = f"mock-image-{self.state.image_generations}-{index + 1}"
                items.append(
                    {
                        "name": media_id,
                        "mediaFormat": "image",
                        "downloadUrl": f"{self.state.media_base_url}/media/{media_id}",
                    }
                )
            return {"ok": True, "status": 200, "data": {"media": items}}

        if "batchAsyncGenerateVideoReferenceImages" in url:
            self.state.omni_generations += 1
            op = f"mock-omni-op-{self.state.omni_generations}"
            return {
                "ok": True,
                "status": 200,
                "data": {"operations": [{"operation": {"name": op}}]},
            }

        if "batchAsyncGenerateVideoStartImage" in url:
            self.state.video_generations += 1
            op = f"mock-video-op-{self.state.video_generations}"
            return {
                "ok": True,
                "status": 200,
                "data": {"operations": [{"operation": {"name": op}}]},
            }

        if "batchCheckAsyncVideoGenerationStatus" in url:
            self.state.polls += 1
            operations = []
            for item in body.get("operations") or []:
                name = (item.get("operation") or {}).get("name")
                media_id = f"mock-video-{name}"
                operations.append(
                    {
                        "operation": {
                            "name": name,
                            "done": True,
                            "metadata": {
                                "video": {
                                    "mediaId": media_id,
                                    "fifeUrl": f"{self.state.media_base_url}/media/{media_id}",
                                }
                            },
                        },
                        "status": "MEDIA_GENERATION_STATUS_SUCCESS",
                    }
                )
            media = []
            for item in body.get("media") or []:
                name = item.get("name")
                media.append(
                    {
                        "name": name,
                        "downloadUrl": f"{self.state.media_base_url}/media/{name}",
                        "mediaMetadata": {
                            "mediaStatus": {
                                "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESS",
                                "done": True,
                            }
                        },
                    }
                )
            return {"ok": True, "status": 200, "data": {"operations": operations, "media": media}}

        if "media.getMediaUrlRedirect" in url:
            media_id = urlparse(url).query.split("name=", 1)[-1]
            return {
                "ok": True,
                "status": 200,
                "finalUrl": f"{self.state.media_base_url}/media/{media_id}",
            }

        return {"ok": True, "status": 200, "data": {}}

    @staticmethod
    def _body(spec: dict) -> dict:
        raw = spec.get("body")
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
