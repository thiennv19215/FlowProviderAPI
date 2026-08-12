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
    done = client.get(f"/v1/status/{task_id}", headers=auth)
    assert done.status_code == 200
    assert done.json()["status"] == "succeeded"
    assert done.json()["outputs"][0]["type"] == "image"


def test_unified_generation_duplicate_submissions_create_new_tasks(client, app, auth):
    payload = {
        "kind": "image",
        "prompt": "two independent cats",
        "provider": "fake",
        "options": {"aspect_ratio": "9:16"},
    }
    headers = {**auth, "Idempotency-Key": "ignored-by-contract"}

    first = client.post("/v1/generations", headers=headers, json=payload)
    second = client.post("/v1/generations", headers=headers, json=payload)

    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] != second.json()["task_id"]
    with app.state.runtime.session_factory() as db:
        rows = list(
            db.scalars(
                select(GenerationJob).where(
                    GenerationJob.id.in_(
                        [first.json()["task_id"], second.json()["task_id"]]
                    )
                )
            )
        )
        assert len(rows) == 2
        assert all(row.idempotency_key is None for row in rows)


def test_unified_generation_openapi_has_no_idempotency_header(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/generations"]["post"]
    assert all(
        parameter.get("name") != "Idempotency-Key"
        for parameter in operation.get("parameters", [])
    )


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
