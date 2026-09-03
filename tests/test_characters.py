import asyncio
import base64
import hashlib
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.workers.job_worker import JobWorker


def make_app(asset_store_path):
    return create_app(Settings(
        env="test", public_base_url="https://provider.test",
        project_store_path=":memory:", asset_store_path=str(asset_store_path), worker_enabled=False,
    ))


def seed_connection(application, raw: bytes = b"character-image"):
    runtime = application.state.runtime
    connection = SimpleNamespace(
        id="connection-1", installation_id="installation-1",
        account_email="character@example.com", connected_at=1, max_slots=4,
        max_image_slots=4, max_video_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100,
    )
    runtime.bridge.ready_connections = lambda **_kwargs: [connection]
    runtime.bridge.pending_count = lambda _connection_id: 0
    account_key = "installation-1\ncharacter@example.com"
    runtime.projects.remember_project(account_key, "project-1", "Character project")
    runtime.mark_project_synced(connection, account_key)
    digest, _path, size = runtime.projects.asset_store.put_bytes(raw, "image/png")
    runtime.projects.record_asset(digest, "image/png", size, "character.png")
    runtime.projects.put_media(
        account_key, "project-1", digest, "media-character", "image/png", "character.png",
        {"media": {"name": "media-character"}}, 200, {},
    )
    return runtime


def test_character_image_and_video_endpoints_are_separate_and_snapshot_refs():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        runtime = seed_connection(application)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "voice_description": "Soft young voice", "reference_media_ids": ["media-character"]},
            )
            assert created.status_code == 201
            character_id = created.json()["id"]
            image = client.post(
                f"/v1/characters/{character_id}/images/generations",
                headers={"Idempotency-Key": "character-image-1"},
                json={"prompt": "Luna in a neon market", "project_id": "project-1"},
            )
            video = client.post(
                f"/v1/characters/{character_id}/videos/generations",
                headers={"Idempotency-Key": "character-video-1"},
                json={"prompt": "Luna walks", "project_id": "project-1", "dialogue": True},
            )
            assert image.status_code == 202
            assert video.status_code == 202
            assert image.json()["jobs"][0]["type"] == "image"
            assert image.json()["jobs"][0]["generation_type"] == "character_image"
            assert video.json()["jobs"][0]["type"] == "video"
            assert video.json()["jobs"][0]["generation_type"] == "character_video"

            calls = []

            async def fake_api(_connection_id, **kwargs):
                calls.append(kwargs)
                if "batchGenerateImages" in kwargs["url"]:
                    return {"status": 200, "data": {"media": [{"name": "generated-image", "image": {"generatedImage": {"fifeUrl": "https://flow-content.google/image"}}}]}}
                return {"status": 200, "data": {"workflows": [{"name": "workflow-character-video", "metadata": {"primaryMediaId": "media-character-video"}}]}}

            runtime.bridge.api_request = fake_api
            asyncio.run(JobWorker(runtime).process_queued_jobs())
            image_call = next(call for call in calls if "batchGenerateImages" in call["url"])
            assert image_call["body"]["requests"][0]["imageInputs"] == [{"name": "media-character", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}]
            assert image_call["body"]["requests"][0]["imageAspectRatio"] == "IMAGE_ASPECT_RATIO_PORTRAIT"
            video_call = next(call for call in calls if "batchAsyncGenerateVideoReferenceImages" in call["url"])
            assert video_call["body"]["requests"][0]["referenceImages"] == [{"mediaId": "media-character", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}]
            assert video_call["body"]["requests"][0]["aspectRatio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"
            assert "Soft young voice" in video_call["body"]["requests"][0]["textInput"]["prompt"]


def test_character_image_accepts_extra_media_and_inline_references():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        runtime = seed_connection(application)
        extra_raw = b"extra-reference"
        extra_digest, _path, extra_size = runtime.projects.asset_store.put_bytes(extra_raw, "image/jpeg")
        runtime.projects.record_asset(extra_digest, "image/jpeg", extra_size, "extra.jpg")
        runtime.projects.put_media(
            "installation-1\ncharacter@example.com", "project-1", extra_digest,
            "media-extra", "image/jpeg", "extra.jpg", {}, 200, {},
        )
        inline = base64.b64encode(b"inline-reference").decode("ascii")
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-character"]},
            )
            character_id = created.json()["id"]
            response = client.post(
                f"/v1/characters/{character_id}/images/generations",
                json={
                    "prompt": "Luna in a neon market",
                    "project_id": "project-1",
                    "reference_media_ids": ["media-extra"],
                    "input_images": [{
                        "image_base64": inline,
                        "mime_type": "image/png",
                        "file_name": "inline.png",
                    }],
                },
            )
            assert response.status_code == 202
            job_id = response.json()["jobs"][0]["id"]

            calls = []

            async def fake_api(_connection_id, **kwargs):
                calls.append(kwargs)
                if "/flow/uploadImage" in kwargs["url"]:
                    return {"status": 200, "data": {"media": {"name": "media-inline"}}}
                return {"status": 200, "data": {"media": [{"name": "generated-image"}]}}

            runtime.bridge.api_request = fake_api
            asyncio.run(JobWorker(runtime).process_queued_jobs())
            assert runtime.projects.get_job(job_id).status == "completed"
            generation = next(call for call in calls if "batchGenerateImages" in call["url"])
            assert generation["body"]["requests"][0]["imageInputs"] == [
                {"name": "media-character", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"},
                {"name": "media-extra", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"},
                {"name": "media-inline", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"},
            ]


def test_character_image_rejects_more_than_eight_combined_references():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        runtime = seed_connection(application)
        account_key = "installation-1\ncharacter@example.com"
        extra_ids = []
        for index in range(8):
            raw = f"extra-{index}".encode()
            digest, _path, size = runtime.projects.asset_store.put_bytes(raw, "image/png")
            runtime.projects.record_asset(digest, "image/png", size, f"extra-{index}.png")
            media_id = f"media-extra-{index}"
            runtime.projects.put_media(account_key, "project-1", digest, media_id, "image/png", f"extra-{index}.png", {}, 200, {})
            extra_ids.append(media_id)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-character"]},
            )
            response = client.post(
                f"/v1/characters/{created.json()['id']}/images/generations",
                json={"prompt": "too many", "reference_media_ids": extra_ids},
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "TOO_MANY_REFERENCES"


def test_character_requires_known_durable_image_and_at_most_three_refs():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        seed_connection(application)
        with TestClient(application) as client:
            unknown = client.post("/v1/characters", json={"name": "Unknown", "reference_media_ids": ["media-not-known"]})
            assert unknown.status_code == 409
            too_many = client.post("/v1/characters", json={"name": "Too many", "reference_media_ids": ["a", "b", "c", "d"]})
            assert too_many.status_code == 422
            no_reference = client.post("/v1/characters", json={"name": "Text only"})
            blocked = client.post(f"/v1/characters/{no_reference.json()['id']}/images/generations", json={"prompt": "draw"})
            assert blocked.status_code == 409
            assert blocked.json()["error"]["code"] == "CHARACTER_REFERENCE_MISSING"


def test_media_upload_persists_source_asset():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        runtime = seed_connection(application)
        raw = b"uploaded-character"

        async def fake_api(_connection_id, **_kwargs):
            return {"status": 200, "data": {"media": {"name": "uploaded-media"}}}

        runtime.bridge.api_request = fake_api
        with TestClient(application) as client:
            response = client.post("/v1/media", json={"project_id": "project-1", "file_name": "upload.png", "mime_type": "image/png", "image_base64": base64.b64encode(raw).decode("ascii")})
        assert response.status_code == 200
        digest = hashlib.sha256(raw).hexdigest()
        assert runtime.projects.asset_store.path_for(digest) is not None
        assert (runtime.projects.asset_store.root / digest).exists()


def test_character_requests_with_different_prompts_are_queued_independently():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        seed_connection(application)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-character"]},
            )
            character_id = created.json()["id"]
            reference = client.get(f"/v1/characters/{character_id}/reference-images/0")
            assert reference.status_code == 200
            assert reference.content == b"character-image"
            assert reference.headers["content-type"] == "image/png"
            first = client.post(
                f"/v1/characters/{character_id}/images/generations",
                json={"prompt": "first"},
            )
            second = client.post(
                f"/v1/characters/{character_id}/images/generations",
                json={"prompt": "second"},
            )
        assert first.status_code == second.status_code == 202
        assert first.json()["jobs"][0]["id"] != second.json()["jobs"][0]["id"]


def test_character_video_rejects_start_media_id_and_dialogue_false_keeps_prompt():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        seed_connection(application)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={
                    "name": "Luna",
                    "voice_description": "Soft young voice",
                    "reference_media_ids": ["media-character"],
                },
            )
            character_id = created.json()["id"]
            rejected = client.post(
                f"/v1/characters/{character_id}/videos/generations",
                json={"prompt": "walk", "start_media_id": "not-allowed"},
            )
            accepted = client.post(
                f"/v1/characters/{character_id}/videos/generations",
                json={"prompt": "walk", "dialogue": False},
            )
        assert rejected.status_code == 422
        assert accepted.status_code == 202
        assert accepted.json()["jobs"][0]["id"]


def test_unknown_character_project_route_is_rejected():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        seed_connection(application)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-character"]},
            )
            character_id = created.json()["id"]
            response = client.post(
                f"/v1/characters/{character_id}/videos/generations",
                json={"prompt": "walk", "project_id": "project-does-not-exist"},
            )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_ROUTE_UNKNOWN"


def test_cached_character_image_and_video_404_invalidate_media_cache():
    for endpoint in ("images", "videos"):
        with tempfile.TemporaryDirectory(dir=".") as td:
            application = make_app(f"{td}/assets")
            runtime = seed_connection(application)
            with TestClient(application) as client:
                created = client.post(
                    "/v1/characters",
                    json={"name": "Luna", "reference_media_ids": ["media-character"]},
                )
                job_response = client.post(
                    f"/v1/characters/{created.json()['id']}/{endpoint}/generations",
                    json={"prompt": "scene", "project_id": "project-1"},
                )
                async def fake_api(_connection_id, **_kwargs):
                    return {"status": 404, "error": "media is stale"}

                runtime.bridge.api_request = fake_api
                asyncio.run(JobWorker(runtime).process_queued_jobs())
                assert runtime.projects.get_job(job_response.json()["jobs"][0]["id"]).status == "failed"
                assert runtime.projects.get_media(
                    "installation-1\ncharacter@example.com", "project-1",
                    runtime.projects.get_character(created.json()["id"]).reference_asset_hashes[0],
                ) is None


def test_worker_reuploads_character_asset_into_target_project():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        runtime = application.state.runtime
        source = SimpleNamespace(
            id="source", installation_id="source-install", account_email="source@example.com",
            connected_at=1, max_slots=4, max_image_slots=4, max_video_slots=3,
            paygate_tier="PAYGATE_TIER_ONE", credits=100,
        )
        target = SimpleNamespace(
            id="target", installation_id="target-install", account_email="target@example.com",
            connected_at=1, max_slots=4, max_image_slots=4, max_video_slots=3,
            paygate_tier="PAYGATE_TIER_ONE", credits=100,
        )
        runtime.bridge.ready_connections = lambda **_kwargs: [source, target]
        runtime.bridge.pending_count = lambda _connection_id: 0
        source_key = "source-install\nsource@example.com"
        target_key = "target-install\ntarget@example.com"
        runtime.projects.remember_project(source_key, "source-project", "Source")
        runtime.projects.remember_project(target_key, "target-project", "Target")
        digest, _path, size = runtime.projects.asset_store.put_bytes(b"source-bytes", "image/png")
        runtime.projects.record_asset(digest, "image/png", size, "source.png")
        runtime.projects.put_media(
            source_key, "source-project", digest, "media-source", "image/png", "source.png", {}, 200, {},
        )
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-source"]},
            )
            job = client.post(
                f"/v1/characters/{created.json()['id']}/images/generations",
                json={"prompt": "target scene", "project_id": "target-project"},
            )
            calls = []

            async def fake_api(_connection_id, **kwargs):
                calls.append(kwargs)
                if "/flow/uploadImage" in kwargs["url"]:
                    return {"status": 200, "data": {"media": {"name": "media-target"}}}
                return {"status": 200, "data": {"media": [{"name": "generated-target"}]}}

            runtime.bridge.api_request = fake_api
            asyncio.run(JobWorker(runtime).process_queued_jobs())
            assert runtime.projects.get_job(job.json()["jobs"][0]["id"]).status == "completed"
        upload = next(call for call in calls if "/flow/uploadImage" in call["url"])
        generation = next(call for call in calls if "batchGenerateImages" in call["url"])
        assert upload["body"]["clientContext"]["projectId"] == "target-project"
        assert generation["body"]["requests"][0]["imageInputs"] == [
            {"name": "media-target", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
        ]


def test_soft_deleted_character_keeps_old_job_status_but_not_catalog_access():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        seed_connection(application)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-character"]},
            )
            character_id = created.json()["id"]
            job = client.post(
                f"/v1/characters/{character_id}/images/generations",
                json={"prompt": "snapshot"},
            )
            job_id = job.json()["jobs"][0]["id"]
            assert client.delete(f"/v1/characters/{character_id}").status_code == 204
            assert client.get(f"/v1/characters/{character_id}").status_code == 404
            status = client.post("/v1/jobs/status", json={"job_ids": [job_id]})
        assert status.status_code == 200
        assert status.json()["jobs"][0]["id"] == job_id


def test_character_edit_after_enqueue_does_not_change_job_snapshot():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        seed_connection(application)
        with TestClient(application) as client:
            created = client.post(
                "/v1/characters",
                json={"name": "Luna", "reference_media_ids": ["media-character"]},
            )
            character_id = created.json()["id"]
            job = client.post(
                f"/v1/characters/{character_id}/images/generations",
                headers={"Idempotency-Key": "snapshot-1"},
                json={"prompt": "original"},
            )
            assert client.patch(
                f"/v1/characters/{character_id}",
                json={"name": "Luna v2", "reference_media_ids": []},
            ).status_code == 200
            stored = application.state.runtime.projects.get_job(job.json()["jobs"][0]["id"])
        assert stored is not None
        assert stored.request_payload["character_id"] == character_id
        assert stored.request_payload["prompt"] == "original"
        assert stored.request_payload["reference_media_ids"] == ["media-character"]


def test_generic_generation_endpoints_reject_character_ids():
    with tempfile.TemporaryDirectory(dir=".") as td:
        application = make_app(f"{td}/assets")
        with TestClient(application) as client:
            image = client.post(
                "/v1/images/generations",
                json={"prompt": "scene", "character_ids": ["char_1"]},
            )
            video = client.post(
                "/v1/videos/generations",
                json={
                    "type": "reference_to_video",
                    "prompt": "scene",
                    "character_ids": ["char_1"],
                    "reference_media_ids": ["media_1"],
                },
            )
        assert image.status_code == video.status_code == 422
