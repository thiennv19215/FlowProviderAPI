import asyncio
import re


def test_image_generation_end_to_end(client, app, auth):
    response = client.post("/v1/images/generations", headers=auth, json={
        "prompt": "a white cat",
        "provider": "fake",
        "aspect_ratio": "1:1",
        "workspace": {"key": "test:image:1"},
    })
    assert response.status_code == 202
    job_id = response.json()["task_id"]
    assert response.json()["status"] == "queued"
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    job = client.get(f"/v1/jobs/{job_id}", headers=auth)
    assert job.status_code == 200
    body = job.json()
    assert body["status"] == "succeeded"
    assert len(body["outputs"]) == 1
    asset_id = body["outputs"][0]["asset_id"]
    content = client.get(f"/v1/assets/{asset_id}/content", headers=auth)
    assert content.content == b"fake-image-bytes"
    assert content.headers["content-type"].startswith("image/png")


def test_video_dispatch_and_poll_survives_db_state(client, app, auth):
    upload = client.post("/v1/assets/uploads", headers=auth, json={"filename":"start.png","content_type":"image/png","type":"image"}).json()
    asset_id = upload["asset"]["id"]
    assert client.put(f"/v1/assets/{asset_id}/content", headers={**auth,"Content-Type":"application/octet-stream"}, content=b"start").status_code == 204
    response = client.post("/v1/videos/image-to-video", headers=auth, json={
        "prompt":"move", "provider":"fake", "input":{"start_asset_id":asset_id}, "workspace":{"key":"test:video:1"}
    })
    job_id=response.json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    mid=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert mid["status"] == "running"
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    done=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert done["status"] == "succeeded"
    assert done["outputs"][0]["type"] == "video"


def test_duplicate_submissions_with_same_key_replay_same_task(client, auth):
    payload={"prompt":"cat","provider":"fake","workspace":{"key":"idem:1"}}
    headers={**auth,"Idempotency-Key":"order-123"}
    first=client.post("/v1/images/generations",headers=headers,json=payload)
    second=client.post("/v1/images/generations",headers=headers,json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]


def test_idempotency_key_rejects_different_payload(client, auth):
    headers={**auth,"Idempotency-Key":"order-conflict"}
    first=client.post("/v1/images/generations",headers=headers,json={"prompt":"cat"})
    second=client.post("/v1/images/generations",headers=headers,json={"prompt":"dog"})
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_server_generates_task_id(client,auth):
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":"fake","workspace":{"key":"task:generated"}})
    assert response.status_code==202
    assert response.json()["task_id"].startswith("job_")


def test_new_asset_ids_are_compact_and_url_safe(client,auth):
    response=client.post("/v1/assets/uploads",headers=auth,json={"filename":"ref.png","content_type":"image/png","type":"image"})
    assert response.status_code==201
    asset_id=response.json()["asset"]["id"]
    assert re.fullmatch(r"asset_[A-Za-z0-9_-]{16}",asset_id)
    assert len(asset_id)==22


def test_structured_auth_error(client):
    response=client.get("/v1/jobs/nope")
    assert response.status_code==401
    error=response.json()["error"]
    assert set(error)=={"status_code","code","message","details","request_id","retryable"}
    assert error["status_code"]==401
    assert error["code"]=="INVALID_API_KEY"
    assert error["details"]==[]
    assert error["request_id"]==response.headers.get("X-Request-Id")
    assert response.headers["WWW-Authenticate"]=="Bearer"


def test_validation_error_reports_all_fields(client,auth):
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"","model":"unknown","output_count":9})
    assert response.status_code==422
    error=response.json()["error"]
    assert error["code"]=="VALIDATION_ERROR"
    assert error["message"]=="Request validation failed."
    assert error["retryable"] is False
    details={item["field"]:item["code"] for item in error["details"]}
    assert details=={"prompt":"INVALID_LENGTH","model":"INVALID_CHOICE","output_count":"OUT_OF_RANGE"}


def test_malformed_json_and_unknown_routes_use_same_error_contract(client,auth):
    malformed=client.post("/v1/images/generations",headers={**auth,"Content-Type":"application/json"},content="{")
    assert malformed.status_code==400
    assert malformed.json()["error"]["code"]=="INVALID_JSON"
    missing=client.get("/v1/not-a-real-endpoint",headers=auth)
    assert missing.status_code==404
    assert missing.json()["error"]["code"]=="ENDPOINT_NOT_FOUND"
    assert set(missing.json()["error"])-{"status_code","code","message","details","request_id","retryable"}==set()


def test_openapi_exposes_typed_generation_and_asset_responses(client):
    schema=client.get("/openapi.json").json()
    assert "/v1/health" in schema["paths"]
    assert not {"/health/live","/health/ready","/api/health"}&set(schema["paths"])
    assert "/v1/videos/image-to-video" in schema["paths"]
    assert "/v1/videos/generations" not in schema["paths"]
    assert set(schema["components"]["schemas"]["ImageToVideoRequest"]["properties"])=={"prompt","start_asset_id","quality","aspect_ratio"}
    image=schema["paths"]["/v1/images/generations"]["post"]
    assert image["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/JobOutput")
    assert image["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert any(parameter.get("name")=="Idempotency-Key" for parameter in image.get("parameters",[]))
    assert "task_id" not in schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]
    assert "workspace" not in schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]
    assert "task_id" in schema["components"]["schemas"]["JobOutput"]["properties"]
    assert "workspace_key" not in schema["components"]["schemas"]["JobOutput"]["properties"]
    assert set(schema["components"]["schemas"]["JobOutput"]["properties"])=={"task_id","status","outputs","error"}
    assert set(schema["components"]["schemas"]["ImageGenerationRequest"]["properties"])=={"prompt","model","aspect_ratio","output_count","reference_asset_ids"}
    model_schema=schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]["model"]
    assert model_schema["enum"]==["banana_pro","banana_2"]
    assert model_schema["default"]=="banana_pro"
    assert schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]["aspect_ratio"]["default"]=="9:16"
    upload=schema["paths"]["/v1/assets/uploads"]["post"]
    assert upload["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/AssetUploadResponse")
