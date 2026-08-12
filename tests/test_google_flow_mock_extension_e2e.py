from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import select

from app.db.models import MediaAsset
from conftest import upload_media

from tests.mock_extension import MockExtensionSocket


class _MediaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b"mock-video-bytes" if "video" in self.path or "omni" in self.path else b"mock-image-bytes"
        content_type = "video/mp4" if payload.startswith(b"mock-video") else "image/png"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class MediaServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


async def _connect_mock(app, media_base_url: str):
    mock = MockExtensionSocket(app.state.runtime.bridge, media_base_url=media_base_url)
    await mock.connect()
    return mock


def _upload_reference(client, auth, filename="reference.png"):
    return upload_media(client,auth,filename=filename,data=b"reference-image-bytes",content_type="image/png")


def test_real_google_flow_stack_image_through_mock_extension(client, app, auth):
    with MediaServer() as media:
        mock = asyncio.run(_connect_mock(app, media.base_url))
        response = client.post(
            "/v1/images/generations",
            headers=auth,
            json={
                "prompt": "a studio product photo",
                "provider": "google_flow",
                "model": "banana_2",
                "aspect_ratio": "1:1",
                "output_count": 2,
                "workspace": {"key": "mock:image:e2e"},
            },
        )
        assert response.status_code == 202
        job_id = response.json()["task_id"]

        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        job = client.get(f"/v1/tasks/{job_id}", headers=auth).json()
        assert job["status"] == "succeeded"
        assert len(job["outputs"]) == 2
        assert mock.state.projects_created == 1
        assert mock.state.image_generations == 1
        # Flow bearer is browser-owned now. Backend fetch RPCs must not request
        # the bearer explicitly; the extension attaches it immediately before
        # executing SW_FETCH / INJECT_PAGE_FETCH.
        assert "GET_BEARER" not in mock.state.rpc_types
        assert "INJECT_RECAPTCHA" in mock.state.rpc_types
        assert "SW_FETCH" in mock.state.rpc_types

        direct_url = f"{media.base_url}/media/mock-image-1-1"
        assert job["outputs"][0]["url"] == direct_url
        content = client.get(
            f"/media/{job['outputs'][0]['media_id']}",
            headers=auth,
            follow_redirects=False,
        )
        assert content.status_code == 307
        assert content.headers["location"] == direct_url
        with app.state.runtime.session_factory() as db:
            asset = db.scalar(select(MediaAsset).where(MediaAsset.id == job["outputs"][0]["media_id"]))
            assert asset.external_url == direct_url
            assert asset.storage_key is None

        referenced = client.post(
            "/v1/images/generations",
            headers=auth,
            json={
                "prompt": "the same product in dramatic lighting",
                "provider": "google_flow",
                "reference_media_ids": [job["outputs"][0]["media_id"]],
            },
        )
        assert referenced.status_code == 202
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        referenced_job = client.get(f"/v1/tasks/{referenced.json()['task_id']}", headers=auth).json()
        assert referenced_job["status"] == "succeeded"
        assert mock.state.projects_created == 1
        assert mock.state.image_generations == 2
        assert mock.state.uploads == 0


def test_real_google_flow_stack_video_and_omni_through_mock_extension(client, app, auth):
    with MediaServer() as media:
        mock = asyncio.run(_connect_mock(app, media.base_url))
        reference_id = _upload_reference(client, auth)

        video = client.post(
            "/v1/videos/image-to-video",
            headers=auth,
            json={
                "prompt": "slow camera push in",
                "provider": "google_flow",
                "start_media_id": reference_id,
                "aspect_ratio": "9:16",
                "workspace": {"key": "mock:video:e2e"},
            },
        )
        assert video.status_code == 202
        video_id = video.json()["task_id"]
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        mid = client.get(f"/v1/tasks/{video_id}", headers=auth).json()
        assert mid["status"] == "running"
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        done = client.get(f"/v1/tasks/{video_id}", headers=auth).json()
        assert done["status"] == "succeeded"
        assert done["outputs"][0]["type"] == "video"
        assert done["outputs"][0]["thumbnail_url"].endswith("-thumbnail")

        omni = client.post(
            "/v1/videos/omni-generations",
            headers=auth,
            json={
                "prompt": "character turns toward camera",
                "provider": "google_flow",
                "duration": 4,
                "aspect_ratio": "9:16",
                "reference_media_ids": [reference_id],
                "workspace": {"key": "mock:omni:e2e"},
            },
        )
        assert omni.status_code == 202
        omni_id = omni.json()["task_id"]
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        omni_done = client.get(f"/v1/tasks/{omni_id}", headers=auth).json()
        assert omni_done["status"] == "succeeded"
        assert omni_done["outputs"][0]["type"] == "video"
        assert omni_done["outputs"][0]["thumbnail_url"].endswith("-thumbnail")

        assert mock.state.projects_created == 1
        assert mock.state.uploads == 1
        assert mock.state.video_generations == 1
        assert mock.state.omni_generations == 1
        assert mock.state.polls >= 2


def test_mock_extension_exposes_ready_account_to_public_accounts_api(client, app, admin_auth):
    with MediaServer() as media:
        asyncio.run(_connect_mock(app, media.base_url))
        response = client.get("/v1/accounts", headers=admin_auth)
        assert response.status_code == 200
        accounts = response.json()["data"]
        assert len(accounts) == 1
        account = accounts[0]
        assert account["ready"] is True
        assert account["email"] == "mock-flow@example.com"
        assert account["credits"] == 500
        assert account["paygate_tier"] == "PAYGATE_TIER_ONE"
