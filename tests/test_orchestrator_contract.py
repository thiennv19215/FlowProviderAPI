import asyncio

from sqlalchemy import func, select

from app.db.models import GenerationJob, MediaAsset


def test_unified_generation_normalizes_provider_options_and_runs(client, app, auth):
    response = client.post(
        "/v1/generations",
        headers=auth,
        json={
            "kind": "image",
            "prompt": "a white cat",
            "provider": "fake",
            "options": {
                "model": "NANO_BANANA_2",
                "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                "count": 2,
            },
        },
    )
    assert response.status_code == 202
    task_id = response.json()["task_id"]

    with app.state.runtime.session_factory() as db:
        job = db.get(GenerationJob, task_id)
        assert job is not None
        assert job.kind == "image"
        assert job.provider == "fake"
        assert job.model == "banana_2"
        assert job.request_payload["aspect_ratio"] == "9:16"
        assert job.request_payload["output_count"] == 2

    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    done = client.get(f"/v1/tasks/{task_id}", headers=auth)
    assert done.status_code == 200
    assert done.json()["status"] == "succeeded"
    assert done.json()["outputs"][0]["type"] == "image"


def test_unified_generation_idempotency_returns_same_durable_task(client, app, auth):
    headers = {**auth, "Idempotency-Key": "flowcanvas:42:image:0"}
    payload = {
        "kind": "image",
        "prompt": "idempotent cat",
        "provider": "fake",
        "options": {"aspect_ratio": "9:16"},
    }

    first = client.post("/v1/generations", headers=headers, json=payload)
    second = client.post("/v1/generations", headers=headers, json=payload)

    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    with app.state.runtime.session_factory() as db:
        rows = list(
            db.scalars(
                select(GenerationJob).where(
                    GenerationJob.idempotency_key == "flowcanvas:42:image:0"
                )
            )
        )
        assert len(rows) == 1


def test_unified_generation_idempotency_rejects_key_reuse_for_different_payload(
    client, auth
):
    headers = {**auth, "Idempotency-Key": "flowcanvas:43:image:0"}
    first = client.post(
        "/v1/generations",
        headers=headers,
        json={"kind": "image", "prompt": "first cat", "provider": "fake"},
    )
    conflict = client.post(
        "/v1/generations",
        headers=headers,
        json={"kind": "image", "prompt": "different cat", "provider": "fake"},
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_idempotent_replay_returns_existing_task_before_reference_revalidation(
    client, app, auth
):
    uploaded = client.post(
        "/v1/media",
        headers=auth,
        files={"file": ("start.png", b"video-start-image", "image/png")},
    )
    assert uploaded.status_code == 201
    media_id = uploaded.json()["media_id"]
    headers = {**auth, "Idempotency-Key": "flowcanvas:44:video:0"}
    payload = {
        "kind": "video",
        "prompt": "move slowly",
        "provider": "fake",
        "media_ids": [media_id],
    }
    first = client.post("/v1/generations", headers=headers, json=payload)
    assert first.status_code == 202

    # Simulate reference storage/database loss after the Provider already
    # accepted this logical POST. An idempotent replay must still recover the
    # durable task instead of revalidating transient input state first.
    with app.state.runtime.session_factory() as db:
        asset = db.get(MediaAsset, media_id)
        assert asset is not None
        db.delete(asset)
        db.commit()

    replay = client.post("/v1/generations", headers=headers, json=payload)
    assert replay.status_code == 202
    assert replay.json()["task_id"] == first.json()["task_id"]


def test_unified_video_requires_localized_start_media(client, auth):
    response = client.post(
        "/v1/generations",
        headers=auth,
        json={"kind": "video", "prompt": "move", "provider": "fake"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_MEDIA_REFERENCE"


def test_reference_upload_is_content_deduplicated_per_client(client, app, auth):
    first = client.post(
        "/v1/media",
        headers=auth,
        files={"file": ("first.png", b"same-reference-bytes", "image/png")},
    )
    second = client.post(
        "/v1/media",
        headers=auth,
        files={"file": ("renamed.png", b"same-reference-bytes", "image/png")},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["media_id"] == second.json()["media_id"]

    with app.state.runtime.session_factory() as db:
        count = db.scalar(select(func.count()).select_from(MediaAsset))
        assert count == 1
        asset = db.get(MediaAsset, first.json()["media_id"])
        assert asset is not None
        assert asset.checksum_sha256 is not None
