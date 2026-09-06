# Hướng dẫn Agent chạy trên VPS gọi FlowProviderAPI

Tài liệu này áp dụng cho Bot/Worker/AI Agent chạy trên cùng VPS hoặc trên một server khác. Contract production giống nhau: gọi qua HTTPS hostname của FlowProviderAPI và dùng business Bearer API key cho mọi `/v1/*` request.

## 1. Topology production hiện tại

Production Compose không publish port `8000` ra host VPS. `cloudflared` kết nối tới service `api:8000` trong Docker network và public HTTPS hostname ra ngoài.

Vì vậy Agent chạy trực tiếp trên host VPS **không nên mặc định gọi `127.0.0.1:8000`**. Với topology hiện tại, dùng:

```text
Agent/Backend -> https://api.shopcongngheso5.io.vn -> Cloudflare Tunnel -> api:8000
```

Nếu sau này Agent được đưa vào cùng Compose network như một service riêng, lúc đó mới dùng hostname nội bộ `http://api:8000` và vẫn phải gửi business API key vì API đang chạy production mode.

## 2. MCP trên VPS

```bash
cd /home/ubuntu/FlowProviderAPI
python3 -m pip install -e ".[dev]"

export FLOW_PROVIDER_MCP_BASE_URL="https://api.shopcongngheso5.io.vn"
export FLOW_PROVIDER_MCP_API_KEY="${FLOW_PROVIDER_BOOTSTRAP_API_KEY}"
export FLOW_PROVIDER_MCP_ALLOWED_ROOTS="/home/ubuntu"
python3 -m app.mcp_server
```

Có thể bỏ `FLOW_PROVIDER_MCP_API_KEY` nếu process MCP đã có `FLOW_PROVIDER_BOOTSTRAP_API_KEY` trong environment; adapter sẽ dùng business key đó làm fallback. Không dùng `FLOW_PROVIDER_EXTENSION_API_KEY` cho MCP.

## 3. Python backend/service-to-service

```python
import base64
import json
import os
import time
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get(
    "FLOW_PROVIDER_BASE_URL",
    "https://api.shopcongngheso5.io.vn",
).rstrip("/")
API_KEY = os.environ["FLOW_PROVIDER_BOOTSTRAP_API_KEY"]
OUTPUT_DIR = Path("/home/ubuntu/media_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def request_api(endpoint: str, payload: dict | None = None, *, idempotency_key: str | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if endpoint.startswith("/v1/"):
        headers["Authorization"] = f"Bearer {API_KEY}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}", data=data, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def wait_for_job(job_id: str, max_wait_seconds: int = 900) -> dict:
    started = time.time()
    while time.time() - started < max_wait_seconds:
        result = request_api("/v1/jobs/status", {"job_ids": [job_id]})
        job = result["jobs"][0]
        if job["status"] == "complete":
            return job
        if job["status"] == "failed":
            raise RuntimeError(json.dumps(job.get("error"), ensure_ascii=False))
        poll_after = result.get("metadata", {}).get("poll_after_seconds") or 10
        time.sleep(max(1, int(poll_after)))
    raise TimeoutError(f"Provider job {job_id} did not finish in time")


def run_example(image_path: str):
    readiness = request_api("/health/ready")
    if readiness.get("status") != "ready":
        raise RuntimeError("FlowProvider is not ready")

    with open(image_path, "rb") as stream:
        image_b64 = base64.b64encode(stream.read()).decode("ascii")

    video = request_api(
        "/v1/videos/generations",
        {
            "type": "reference_to_video",
            "prompt": "Cinematic natural movement, stable identity",
            "input_images": [
                {
                    "image_base64": image_b64,
                    "mime_type": "image/png",
                    "file_name": Path(image_path).name,
                }
            ],
            "aspect_ratio": "9:16",
            "duration_seconds": 8,
        },
        idempotency_key="example-video-001",
    )
    job_id = video["jobs"][0]["id"]
    completed = wait_for_job(job_id)
    print(completed)
```

## 4. Quy tắc vận hành

- Key chỉ nằm trong secret/environment của backend, không hard-code vào source hoặc frontend.
- Luôn dùng `Idempotency-Key` cho paid generation.
- Lưu `jobs[].id` ngay sau response `202`.
- Poll `/v1/jobs/status` theo `metadata.poll_after_seconds`.
- Không tạo job paid thứ hai khi job cũ còn `queued`/`running`.
- Nếu lỗi có `outcome_unknown=true`, phải đối soát job cũ trước khi gửi lại.
- Tải media hoàn thành về storage riêng sớm vì signed URL có thể hết hạn.
- Preflight bằng `/health/ready`; `/health/live` chỉ dùng cho liveness.

## 5. Chạy 24/7

Systemd/Kubernetes/Docker có thể quản lý Agent độc lập. Secret nên đi qua environment file permission `600`, secret manager hoặc runtime secret mechanism. Restart Agent không được làm mất Provider `job_id` đang theo dõi; lưu ID vào DB của ứng dụng gọi trước khi tiếp tục workflow.

Nếu cần chạy Agent trong chính Compose network để tránh đường vòng qua public hostname, hãy thêm Agent thành service riêng và gọi `http://api:8000`; không publish `8000` ra Internet chỉ để phục vụ Agent nội bộ.
