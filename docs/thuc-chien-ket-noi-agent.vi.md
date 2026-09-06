# Sổ tay thực chiến: kết nối Agent/Backend với FlowProviderAPI

FlowProviderAPI production được thiết kế để một backend, worker hoặc MCP adapter gọi qua HTTPS. Mọi endpoint nghiệp vụ `/v1/*` yêu cầu business Bearer API key; Chrome extension dùng credential riêng và không được chia sẻ cho caller.

## 1. Mô hình kết nối

```text
Server/Laptop/Agent
        |
        | HTTPS + Authorization: Bearer <business key>
        v
Cloudflare Tunnel -> FlowProviderAPI -> Chrome Extension -> Google Flow
```

Production hostname hiện dùng trong deployment example:

```text
https://api.shopcongngheso5.io.vn
```

Không dùng raw VPS IP/port như contract production. Compose hiện không publish port `8000` ra host.

## 2. MCP remote

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Projects\\FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "https://api.shopcongngheso5.io.vn",
        "FLOW_PROVIDER_MCP_API_KEY": "fpa_prod_REPLACE_WITH_SECRET",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Projects"
      }
    }
  }
}
```

Giữ key trong secret/runtime config; không commit config có production key.

## 3. Python S2S tối thiểu

```python
import base64
import json
import os
import time
import urllib.request
from pathlib import Path

PROVIDER_URL = os.environ.get(
    "FLOW_PROVIDER_BASE_URL",
    "https://api.shopcongngheso5.io.vn",
).rstrip("/")
API_KEY = os.environ["FLOW_PROVIDER_BOOTSTRAP_API_KEY"]


def api(endpoint: str, payload: dict | None = None, *, idempotency_key: str | None = None):
    headers = {"Accept": "application/json"}
    data = None
    if endpoint.startswith("/v1/"):
        headers["Authorization"] = f"Bearer {API_KEY}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        PROVIDER_URL + endpoint,
        data=data,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def wait_job(job_id: str, timeout: int = 900):
    started = time.time()
    while time.time() - started < timeout:
        result = api("/v1/jobs/status", {"job_ids": [job_id]})
        job = result["jobs"][0]
        if job["status"] == "complete":
            return job
        if job["status"] == "failed":
            raise RuntimeError(json.dumps(job.get("error"), ensure_ascii=False))
        time.sleep(result.get("metadata", {}).get("poll_after_seconds") or 10)
    raise TimeoutError(job_id)


def generate_video(image_path: str):
    if api("/health/ready").get("status") != "ready":
        raise RuntimeError("Provider is not ready")

    path = Path(image_path)
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    response = api(
        "/v1/videos/generations",
        {
            "type": "reference_to_video",
            "prompt": "Natural motion, cinematic camera, preserve identity",
            "input_images": [{
                "image_base64": image_b64,
                "mime_type": "image/png",
                "file_name": path.name,
            }],
            "duration_seconds": 8,
            "aspect_ratio": "9:16",
        },
        idempotency_key="order-123-video-1",
    )
    return wait_job(response["jobs"][0]["id"])
```

## 4. Node.js S2S tối thiểu

```javascript
const providerUrl = process.env.FLOW_PROVIDER_BASE_URL ||
  'https://api.shopcongngheso5.io.vn';
const apiKey = process.env.FLOW_PROVIDER_BOOTSTRAP_API_KEY;

async function postJson(path, body, idempotencyKey) {
  const headers = {
    'Authorization': `Bearer ${apiKey}`,
    'Content-Type': 'application/json',
  };
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  const response = await fetch(`${providerUrl}${path}`, {
    method: 'POST', headers, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(data));
  return data;
}
```

## 5. Workflow production chuẩn

1. `GET /health/ready` để biết có account Flow đang phục vụ được hay không.
2. Tạo image/video bằng endpoint tương ứng và business Bearer key.
3. Với paid video, luôn gửi `Idempotency-Key` duy nhất cho logical request.
4. Persist `jobs[].id` ngay khi nhận `202`.
5. Poll `POST /v1/jobs/status` theo `metadata.poll_after_seconds`.
6. `queued`/`running` là bình thường; không tạo request paid mới để “thử lại”.
7. Khi `failed`, đọc `retryable` và `outcome_unknown`.
8. Khi `complete`, lưu output về storage của ứng dụng sớm.

## 6. Input ảnh và multi-account

Đối với video, caller nên gửi raw image qua `input_images` Base64. Provider lưu asset content-addressed, SHA-256 deduplicate và có thể rehydrate sang account/project khác khi worker failover. Đây là contract phù hợp hơn việc caller tự giữ Google Flow `media_id`.

- frames-to-video: 1-2 ảnh.
- reference-to-video/R2V/Omni: 1-8 ảnh.

## 7. Credential boundary

- `FLOW_PROVIDER_BOOTSTRAP_API_KEY`: chỉ cho backend/MCP đáng tin cậy gọi `/v1/*`.
- `FLOW_PROVIDER_EXTENSION_API_KEY`: chỉ cho Provider ↔ Chrome extension.
- Không dùng chung hai key.
- Không đưa bất kỳ production key nào ra frontend/browser application của người dùng.

Chi tiết schema/error contract: `docs/integration-guide.vi.md`. MCP-specific workflow: `docs/mcp-agent.vi.md`.
