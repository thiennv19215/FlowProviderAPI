import asyncio
import re

from conftest import upload_media

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
    job = client.get(f"/v1/tasks/{job_id}", headers=auth)
    assert job.status_code == 200
    body = job.json()
    assert body["status"] == "succeeded"
    assert len(body["outputs"]) == 1
    assert body["outputs"][0]["thumbnail_url"] is None
    asset_id = body["outputs"][0]["id"]
    content = client.get(f"/media/{asset_id}", headers=auth)
    assert content.content == b"fake-image-bytes"
    assert content.headers["content-type"].startswith("image/png")


def test_video_dispatch_and_poll_survives_db_state(client, app, auth):
    asset_id=upload_media(client,auth,filename="start.png",data=b"start",content_type="image/png")
    response = client.post("/v1/videos/image-to-video", headers=auth, json={
        "prompt":"move", "provider":"fake", "start_media_id":asset_id, "workspace":{"key":"test:video:1"}
    })
    job_id=response.json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    mid=client.get(f"/v1/tasks/{job_id}",headers=auth).json()
    assert mid["status"] == "running"
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    done=client.get(f"/v1/tasks/{job_id}",headers=auth).json()
    assert done["status"] == "succeeded"
    assert done["outputs"][0]["type"] == "video"


def test_duplicate_submissions_always_create_new_tasks(client, auth):
    payload={"prompt":"cat","provider":"fake","workspace":{"key":"idem:1"}}
    headers={**auth,"Idempotency-Key":"order-123"}
    first=client.post("/v1/images/generations",headers=headers,json=payload)
    second=client.post("/v1/images/generations",headers=headers,json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] != second.json()["task_id"]


def test_server_generates_task_id(client,auth):
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":"fake","workspace":{"key":"task:generated"}})
    assert response.status_code==202
    assert response.json()["task_id"].startswith("job_")


def test_new_media_ids_are_compact_and_url_safe(client,auth):
    response=client.post("/v1/media",headers=auth,files={"file":("ref.png",b"image","image/png")})
    assert response.status_code==201
    media_id=response.json()["id"]
    assert re.fullmatch(r"media_[A-Za-z0-9_-]{16}",media_id)
    assert len(media_id)==22


def test_structured_auth_error(client):
    response=client.get("/v1/tasks/nope")
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
    assert "/v1/tasks/{task_id}" in schema["paths"]
    assert "/v1/jobs/{task_id}" not in schema["paths"]
    assert set(schema["components"]["schemas"]["ImageToVideoRequest"]["properties"])=={"prompt","start_media_id","quality","aspect_ratio"}
    image=schema["paths"]["/v1/images/generations"]["post"]
    assert image["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/JobOutput")
    assert image["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert all(parameter.get("name")!="Idempotency-Key" for parameter in image.get("parameters",[]))
    assert "task_id" not in schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]
    assert "workspace" not in schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]
    assert "task_id" in schema["components"]["schemas"]["JobOutput"]["properties"]
    assert "workspace_key" not in schema["components"]["schemas"]["JobOutput"]["properties"]
    assert set(schema["components"]["schemas"]["JobOutput"]["properties"])=={"task_id","status","outputs","error"}
    assert set(schema["components"]["schemas"]["ImageGenerationRequest"]["properties"])=={"prompt","model","aspect_ratio","output_count","reference_media_ids"}
    model_schema=schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]["model"]
    assert model_schema["enum"]==["banana_pro","banana_2"]
    assert model_schema["default"]=="banana_pro"
    assert schema["components"]["schemas"]["ImageGenerationRequest"]["properties"]["aspect_ratio"]["default"]=="9:16"
    upload=schema["paths"]["/v1/media"]["post"]
    assert upload["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/MediaOutput")
    assert "/v1/media/uploads" not in schema["paths"]
    assert "/v1/media/{media_id}/content" not in schema["paths"]
    assert "/media/{media_id}" not in schema["paths"]
    assert "/v1/assets/uploads" not in schema["paths"]
    assert upload["tags"]==["Media"]


def test_upload_media_in_one_request(client,auth):
    response=client.post("/v1/media",headers=auth,files={"file":("reference.png",b"image-bytes","image/png")})
    assert response.status_code==201
    media=response.json()
    assert set(media)>={"id","object","type","status","mime_type","url"}
    assert media["object"]=="media"
    assert media["type"]=="image"
    assert media["status"]=="ready"
    assert media["url"].endswith(f"/media/{media['id']}")


def test_legacy_media_upload_routes_are_removed(client,auth):
    assert client.post("/v1/media/uploads",headers=auth,json={}).status_code==405
    assert client.put("/v1/media/media_unused/content",headers=auth,content=b"").status_code==404
    assert client.post("/v1/media/media_unused/complete",headers=auth).status_code==404


def test_media_upload_body_is_limited_before_multipart_parsing(client,app,auth):
    app.state.runtime.settings.max_upload_bytes=1024
    response=client.post("/v1/media",headers=auth,files={"file":("too-large.png",b"x"*(2*1024*1024),"image/png")})
    assert response.status_code==413
    assert response.json()["error"]["code"]=="MEDIA_TOO_LARGE"
