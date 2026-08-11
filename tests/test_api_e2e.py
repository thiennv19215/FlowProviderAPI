import asyncio


def test_image_generation_end_to_end(client, app, auth):
    response = client.post("/v1/images/generations", headers=auth, json={
        "prompt": "a white cat",
        "provider": "fake",
        "aspect_ratio": "1:1",
        "workspace": {"key": "test:image:1"},
    })
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert response.json()["status"] == "queued"
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    job = client.get(f"/v1/jobs/{job_id}", headers=auth)
    assert job.status_code == 200
    body = job.json()
    assert body["status"] == "succeeded"
    assert len(body["outputs"]) == 1
    asset_id = body["outputs"][0]["id"]
    content = client.get(f"/v1/assets/{asset_id}/content", headers=auth)
    assert content.content == b"fake-image-bytes"
    assert content.headers["content-type"].startswith("image/png")


def test_video_dispatch_and_poll_survives_db_state(client, app, auth):
    upload = client.post("/v1/assets/uploads", headers=auth, json={"filename":"start.png","content_type":"image/png","type":"image"}).json()
    asset_id = upload["asset"]["id"]
    assert client.put(f"/v1/assets/{asset_id}/content", headers={**auth,"Content-Type":"application/octet-stream"}, content=b"start").status_code == 204
    response = client.post("/v1/videos/generations", headers=auth, json={
        "prompt":"move", "provider":"fake", "input":{"start_asset_id":asset_id}, "workspace":{"key":"test:video:1"}
    })
    job_id=response.json()["id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    mid=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert mid["status"] == "running"
    assert mid["stage"] == "provider_running"
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    done=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert done["status"] == "succeeded"
    assert done["outputs"][0]["type"] == "video"


def test_idempotency_returns_same_job(client, auth):
    payload={"prompt":"cat","provider":"fake","workspace":{"key":"idem:1"}}
    headers={**auth,"Idempotency-Key":"order-123"}
    first=client.post("/v1/images/generations",headers=headers,json=payload)
    second=client.post("/v1/images/generations",headers=headers,json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_structured_auth_error(client):
    response=client.get("/v1/jobs/nope")
    assert response.status_code==401
    assert response.json()["error"]["code"]=="INVALID_API_KEY"
    assert response.headers.get("X-Request-Id")
