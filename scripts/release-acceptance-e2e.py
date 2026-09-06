from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("FLOW_PROVIDER_ACCEPTANCE_BASE_URL", "https://api.shopcongngheso5.io.vn").rstrip("/")
API_KEY = os.environ.get("FLOW_PROVIDER_BOOTSTRAP_API_KEY", "").strip()
TARGET_SHA = os.environ.get("FLOW_PROVIDER_ACCEPTANCE_TARGET_SHA", "unknown").strip()
PRODUCTION_REPO = Path(os.environ.get("FLOW_PROVIDER_PRODUCTION_REPO", "/home/ubuntu/FlowProviderAPI"))
ENV_FILE = PRODUCTION_REPO / ".env.production"
COMPOSE_FILE = PRODUCTION_REPO / "compose.production.yaml"

if not API_KEY:
    raise SystemExit("FLOW_PROVIDER_BOOTSTRAP_API_KEY is required")


def _decode_json(data: bytes) -> Any:
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def http_json(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    authenticated: bool = True,
    headers: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
    timeout: int = 30,
) -> tuple[int, Any, dict[str, str]]:
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if authenticated:
        request_headers["Authorization"] = f"Bearer {API_KEY}"
    if headers:
        request_headers.update(headers)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = _decode_json(response.read())
            response_headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
        try:
            body = _decode_json(raw)
        except Exception:
            body = raw.decode("utf-8", errors="replace")
        response_headers = {k.lower(): v for k, v in exc.headers.items()}
    if status not in expected:
        raise AssertionError(f"{method} {path} returned {status}, expected {expected}: {body}")
    return status, body, response_headers


def wait_health(path: str, *, timeout_seconds: int = 180, expect_ready: bool = False) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last: Any = None
    while time.monotonic() < deadline:
        try:
            _status, body, _headers = http_json("GET", path, authenticated=False, expected=(200,), timeout=10)
            if isinstance(body, dict):
                if not expect_ready or body.get("status") == "ready":
                    return body
                last = body
        except Exception as exc:
            last = str(exc)
        time.sleep(5)
    raise AssertionError(f"{path} did not become healthy in time; last={last}")


def assert_health_privacy() -> None:
    _status, body, _headers = http_json("GET", "/api/health", authenticated=False, expected=(200,), timeout=10)
    assert isinstance(body, dict), body
    encoded = json.dumps(body).lower()
    forbidden = ["account_email", "@gmail.com", '"credits"', '"last_error"', '"accounts"']
    leaked = [token for token in forbidden if token in encoded]
    if leaked:
        raise AssertionError(f"public health leaked account detail fields: {leaked}: {body}")
    jobs = body.get("jobs")
    if not isinstance(jobs, dict) or not {"queued", "dispatching", "running"}.issubset(jobs):
        raise AssertionError(f"public health missing durable queue telemetry: {body}")
    print(f"health privacy/queue telemetry: PASS ({body.get('status')}, providers={body.get('provider_accounts')})")


def auth_smoke() -> None:
    fake_id = "acceptance_missing_job"
    _status, body, headers = http_json(
        "POST",
        "/v1/jobs/status",
        {"job_ids": [fake_id]},
        authenticated=False,
        expected=(401,),
    )
    if not isinstance(body, dict) or body.get("error", {}).get("code") != "INVALID_API_KEY":
        raise AssertionError(f"unauthenticated business API did not return INVALID_API_KEY: {body}")
    if "bearer" not in headers.get("www-authenticate", "").lower():
        raise AssertionError("401 response is missing WWW-Authenticate: Bearer")

    _status, body, _headers = http_json(
        "POST",
        "/v1/jobs/status",
        {"job_ids": [fake_id]},
        authenticated=True,
        expected=(200,),
    )
    job = body.get("jobs", [{}])[0] if isinstance(body, dict) else {}
    if job.get("error", {}).get("code") != "JOB_NOT_FOUND":
        raise AssertionError(f"authenticated business API did not reach application: {body}")
    print("business API authentication smoke: PASS")


def poll_job(job_id: str, *, timeout_seconds: int, restart_when_running: bool = False) -> dict:
    deadline = time.monotonic() + timeout_seconds
    restarted = False
    previous = None
    while time.monotonic() < deadline:
        _status, body, _headers = http_json(
            "POST",
            "/v1/jobs/status",
            {"job_ids": [job_id]},
            authenticated=True,
            expected=(200,),
            timeout=30,
        )
        if not isinstance(body, dict) or not body.get("jobs"):
            raise AssertionError(f"invalid job-status response for {job_id}: {body}")
        job = body["jobs"][0]
        status = job.get("status")
        if status != previous:
            print(f"job {job_id}: {status}")
            previous = status

        if restart_when_running and status == "running" and not restarted:
            restart_api()
            wait_health("/health/live", timeout_seconds=120)
            wait_health("/health/ready", timeout_seconds=180, expect_ready=True)
            restarted = True
            print(f"job {job_id}: API restart/reconnect gate PASS")
            continue

        if status == "complete":
            if restart_when_running and not restarted:
                raise AssertionError("video completed before the restart resilience gate was exercised")
            return job
        if status == "failed":
            raise AssertionError(f"job {job_id} failed: {job.get('error')}")

        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        delay = metadata.get("poll_after_seconds")
        time.sleep(float(delay) if isinstance(delay, (int, float)) and delay > 0 else 10.0)
    raise AssertionError(f"job {job_id} did not reach terminal state within {timeout_seconds}s")


def restart_api() -> None:
    print("Restarting API container while paid video job is running...")
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "-f",
            str(COMPOSE_FILE),
            "restart",
            "api",
        ],
        cwd=PRODUCTION_REPO,
        check=True,
        timeout=120,
    )


def create_job(path: str, payload: dict, idempotency_key: str) -> tuple[str, dict]:
    _status, body, _headers = http_json(
        "POST",
        path,
        payload,
        authenticated=True,
        expected=(202,),
        headers={"Idempotency-Key": idempotency_key},
        timeout=30,
    )
    if not isinstance(body, dict) or not body.get("jobs"):
        raise AssertionError(f"generation did not return a Provider job: {body}")
    job_id = str(body["jobs"][0].get("id") or "")
    if not job_id.startswith("job_"):
        raise AssertionError(f"invalid job id: {body}")
    return job_id, body


def download_probe(url: str, *, minimum_bytes: int, label: str) -> tuple[bytes, str]:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise AssertionError(f"{label} media URL is invalid: {url!r}")
    request = urllib.request.Request(url, headers={"Range": "bytes=0-262143", "User-Agent": "FlowProviderAcceptance/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        data = response.read(262144)
    if len(data) < minimum_bytes:
        raise AssertionError(f"{label} media download returned only {len(data)} bytes")
    print(f"{label} media download: PASS ({len(data)} probe bytes, {content_type or 'unknown content-type'})")
    return data, content_type


def image_mime(data: bytes, content_type: str) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "acceptance.png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "acceptance.jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "acceptance.webp"
    if content_type in {"image/png", "image/jpeg", "image/webp"}:
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[content_type]
        return content_type, f"acceptance.{suffix}"
    raise AssertionError(f"unsupported generated image format: {content_type}")


def main() -> None:
    print(f"Release acceptance target: {TARGET_SHA}")
    live = wait_health("/health/live", timeout_seconds=120)
    ready = wait_health("/health/ready", timeout_seconds=180, expect_ready=True)
    print(f"public liveness/readiness: PASS ({live.get('status')}, providers={ready.get('provider_accounts')})")
    assert_health_privacy()
    auth_smoke()

    suffix = TARGET_SHA[:12] if TARGET_SHA and TARGET_SHA != "unknown" else str(int(time.time()))
    image_payload = {
        "prompt": "A photorealistic ceramic coffee mug on a clean wooden table, soft natural window light, premium product photography, no text, no logo",
        "model": "pro",
        "aspect_ratio": "9:16",
        "variant_count": 1,
        "input_images": [],
    }
    image_key = f"release-acceptance-{suffix}-image-v1"
    image_job_id, _ = create_job("/v1/images/generations", image_payload, image_key)
    image_job = poll_job(image_job_id, timeout_seconds=240)
    image_media = image_job.get("media") or []
    if not image_media:
        raise AssertionError(f"completed image job has no media: {image_job}")
    image_bytes, image_content_type = download_probe(
        image_media[0].get("url"),
        minimum_bytes=1024,
        label="image",
    )
    mime_type, file_name = image_mime(image_bytes, image_content_type)
    print(f"real image generation: PASS ({image_job_id})")

    video_payload = {
        "type": "reference_to_video",
        "prompt": "Slow cinematic camera push-in with subtle natural parallax and gentle light movement. Keep the product identity consistent. No text, no logo.",
        "input_images": [
            {
                "image_base64": base64.b64encode(image_bytes).decode("ascii"),
                "mime_type": mime_type,
                "file_name": file_name,
            }
        ],
        "aspect_ratio": "9:16",
        "duration_seconds": 8,
    }
    video_key = f"release-acceptance-{suffix}-video-v1"
    video_job_id, first_response = create_job("/v1/videos/generations", video_payload, video_key)
    replay_job_id, replay_response = create_job("/v1/videos/generations", video_payload, video_key)
    if replay_job_id != video_job_id:
        raise AssertionError(
            f"idempotency replay created a different job: {video_job_id} != {replay_job_id}; "
            f"first={first_response}, replay={replay_response}"
        )
    print(f"paid video idempotency immediate replay: PASS ({video_job_id})")

    video_job = poll_job(video_job_id, timeout_seconds=900, restart_when_running=True)
    video_media = video_job.get("media") or []
    if not video_media:
        raise AssertionError(f"completed video job has no media: {video_job}")
    download_probe(video_media[0].get("url"), minimum_bytes=4096, label="video")

    terminal_replay_job_id, _ = create_job("/v1/videos/generations", video_payload, video_key)
    if terminal_replay_job_id != video_job_id:
        raise AssertionError("terminal idempotency replay did not return the original paid video job")
    print(f"paid video terminal replay: PASS ({video_job_id})")

    _status, final_status, _headers = http_json(
        "POST",
        "/v1/jobs/status",
        {"job_ids": [image_job_id, video_job_id]},
        authenticated=True,
        expected=(200,),
    )
    statuses = {job.get("id"): job.get("status") for job in final_status.get("jobs", [])}
    if statuses.get(image_job_id) != "complete" or statuses.get(video_job_id) != "complete":
        raise AssertionError(f"final durable job state is not complete: {final_status}")

    print("RELEASE_ACCEPTANCE_E2E=PASS")
    print(json.dumps({
        "target_sha": TARGET_SHA,
        "image_job_id": image_job_id,
        "video_job_id": video_job_id,
        "idempotency_replay_same_job": True,
        "restart_resilience": True,
        "media_download": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RELEASE_ACCEPTANCE_E2E=FAIL: {exc}", file=sys.stderr)
        raise
