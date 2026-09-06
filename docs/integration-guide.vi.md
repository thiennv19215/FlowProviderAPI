# Tích hợp FlowProviderAPI từ backend khác

Tài liệu này là contract HTTP dành cho tích hợp service-to-service của FlowProviderAPI hiện tại. Provider nhận request nghiệp vụ, lưu Provider job bền vững trong SQLite, điều phối Chrome extension đang đăng nhập Google Flow và trả trạng thái chuẩn hóa cho backend gọi.

## 1. Production

```text
Base URL: https://api.shopcongngheso5.io.vn
OpenAPI:  https://api.shopcongngheso5.io.vn/openapi.json
Swagger:  https://api.shopcongngheso5.io.vn/docs
```

### Xác thực

Mọi endpoint nghiệp vụ dưới `/v1/*` ở production bắt buộc Bearer API key:

```http
Authorization: Bearer <FLOW_PROVIDER_BOOTSTRAP_API_KEY>
Content-Type: application/json
```

Key này chỉ cấp cho backend/server tin cậy. Không dùng `FLOW_PROVIDER_EXTENSION_API_KEY` để gọi API nghiệp vụ; key extension là credential riêng giữa Provider và Chrome connector. Hai key production bắt buộc khác nhau.

Thiếu hoặc sai Bearer key trả `401 INVALID_API_KEY` và `WWW-Authenticate: Bearer`.

Có thể gửi thêm `X-Request-Id`; nếu bỏ qua, Provider tự sinh request ID và trả lại trong response.

## 2. Endpoint nghiệp vụ hiện tại

| Method | Endpoint | Chức năng |
|---|---|---|
| `POST` | `/v1/media` | Upload/cache ảnh vào managed hoặc explicit Flow project. |
| `POST` | `/v1/images/generations` | Tạo image job. |
| `POST` | `/v1/videos/generations` | Tạo video job: frames-to-video hoặc reference-to-video. |
| `POST` | `/v1/jobs/status` | Đọc trạng thái Provider job bền vững từ DB. |
| `POST` | `/v1/characters` | Tạo Character từ media đã upload. |
| `GET` | `/v1/characters` | Liệt kê Character. |
| `GET/PATCH/DELETE` | `/v1/characters/{id}` | Đọc/sửa/xóa mềm Character. |
| `GET` | `/v1/characters/{id}/reference-images/{index}` | Đọc ảnh tham chiếu Character. |
| `POST` | `/v1/characters/{id}/images/generations` | Tạo image job theo Character. |
| `POST` | `/v1/characters/{id}/videos/generations` | Tạo video job theo Character. |

Không dựa vào endpoint cũ như `/v1/projects` hoặc `/v1/videos/status`; chúng không thuộc public OpenAPI hiện tại. Managed project được Provider tự xử lý.

## 3. Health contract

```http
GET /health/live
GET /health/ready
GET /api/health
```

- `/health/live`: process API đang sống; dùng cho container/orchestrator liveness.
- `/health/ready`: chỉ trả `200` khi DB sẵn sàng **và** có ít nhất một Google Flow connector sẵn sàng. Khi chưa có connector, trả `503` với `status: waiting_for_provider`.
- Health public chỉ trả số liệu tổng hợp, không trả email tài khoản, credit chi tiết hay lỗi nội bộ của extension.

Không dùng `/health/live` để quyết định có được gửi generation job hay không; backend tích hợp nên dùng `/health/ready` khi cần preflight readiness.

## 4. Luồng tích hợp khuyến nghị

```text
Backend A
  -> POST /v1/images/generations hoặc /v1/videos/generations
  <- 202 + jobs[].id
  -> POST /v1/jobs/status {job_ids:[...]}
  <- queued | running | complete | failed
```

Provider tự chọn account/extension đủ điều kiện, tự lấy hoặc tạo managed project, lưu mapping project/media và giữ ownership theo account. Với ảnh tham chiếu cho video, ưu tiên gửi `input_images` Base64 để Provider có thể SHA-256 deduplicate và cân bằng nhiều account an toàn.

### Idempotency

Các endpoint tạo image/video hỗ trợ:

```http
Idempotency-Key: <unique-key-per-logical-request>
```

Retry cùng key + cùng payload trả lại job cũ. Dùng cùng key với payload khác trả `409 IDEMPOTENCY_KEY_REUSED`.

Backend nên luôn dùng idempotency key cho paid video request để tránh tạo trùng khi mạng timeout hoặc response bị mất.

## 5. Tạo ảnh

```http
POST /v1/images/generations
Authorization: Bearer <key>
Idempotency-Key: img-order-123-scene-1
```

```json
{
  "prompt": "product photo, clean studio lighting",
  "model": "pro",
  "aspect_ratio": "9:16",
  "variant_count": 1,
  "input_images": []
}
```

Giá trị public ổn định:

- `model`: `pro` hoặc `v2`.
- `aspect_ratio`: `1:1`, `16:9`, `9:16`.
- `variant_count`: 1-4.
- `reference_media_ids` + `input_images`: tối đa 8 ảnh tổng cộng.
- `prompt`: 1-12000 ký tự.

Response là `202` và Provider job, không phải kết quả ảnh đồng bộ.

## 6. Tạo video

### Frames-to-video / image-to-video

```json
{
  "type": "frames_to_video",
  "prompt": "slow cinematic camera push-in",
  "input_images": [
    {
      "image_base64": "<base64>",
      "mime_type": "image/png",
      "file_name": "start.png"
    }
  ],
  "aspect_ratio": "9:16",
  "duration_seconds": 8
}
```

Các alias tương thích gồm `frames`, `start_to_video`, `image_to_video`, `i2v`, `omni_i2v`. Với video, `input_images` Base64 là input được khuyến nghị/bắt buộc theo schema hiện tại để hỗ trợ multi-account balancing an toàn.

### Reference-to-video / Omni

```json
{
  "type": "reference_to_video",
  "prompt": "same character walks through the showroom",
  "input_images": [
    {
      "image_base64": "<base64>",
      "mime_type": "image/png",
      "file_name": "character.png"
    }
  ],
  "aspect_ratio": "9:16",
  "duration_seconds": 8
}
```

Các alias tương thích gồm `ingredients`, `references`, `omni`, `r2v`, `omni_r2v`. Omni nhận 1-8 `input_images` và không nhận `reference_media_ids` trong contract hiện tại.

## 7. Poll Provider job

```http
POST /v1/jobs/status
Authorization: Bearer <key>
```

```json
{
  "job_ids": ["job_abc123"]
}
```

Response chuẩn hóa:

```json
{
  "jobs": [
    {
      "id": "job_abc123",
      "type": "video",
      "generation_type": "reference_to_video",
      "status": "running",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "request_id": "req_...",
    "project_id": null,
    "routing_scope": null,
    "poll_after_seconds": 10,
    "counts": {
      "queued": 0,
      "running": 1,
      "complete": 0,
      "failed": 0
    },
    "done": false
  }
}
```

Public status chỉ có:

```text
queued -> running -> complete | failed
```

Khi `failed`, đọc:

```json
{
  "code": "...",
  "message": "...",
  "retryable": false,
  "outcome_unknown": false
}
```

Nếu `outcome_unknown=true`, không tự tạo lại paid video cho đến khi đã đối soát; upstream có thể đã nhận request trước khi timeout.

## 8. Timeout hiện tại

- Image job: `IMAGE_TIMEOUT` sau 120 giây.
- Video chờ queue: `QUEUE_TIMEOUT` sau 180 giây.
- Video đang render/poll: `VIDEO_POLL_TIMEOUT` sau 600 giây theo runtime default.
- `metadata.poll_after_seconds` hiện là 10 giây khi job chưa terminal.

Backend không nên poll nhanh hơn giá trị Provider trả về.

## 9. Multi-account và credit

Provider giữ route project/media theo Google account, không gửi project-bound media sang account tùy ý. Với request không bị sticky scope khóa và input có thể rehydrate, Provider có thể chuyển sang account đủ credit khác theo business logic hiện tại.

Paid video reserve credit trước khi dispatch. Sau paid attempt, số dư account được invalidated và phải refresh lại trước khi tiếp tục paid routing. Mục tiêu là tránh nhiều request đồng thời tiêu cùng một balance nhìn thấy.

## 10. Error envelope

Lỗi public có dạng:

```json
{
  "error": {
    "status_code": 503,
    "code": "PROVIDER_ACCOUNT_UNAVAILABLE",
    "message": "...",
    "details": [],
    "request_id": "req_...",
    "retryable": true
  }
}
```

Backend tích hợp phải quyết định retry theo `retryable`, không dựa riêng vào HTTP status.

## 11. Checklist cho server gọi Provider

1. Lưu `FLOW_PROVIDER_BOOTSTRAP_API_KEY` ở secret backend, không đưa ra frontend/browser.
2. Gửi Bearer key cho mọi `/v1/*` request production.
3. Dùng `Idempotency-Key` cho generation request, đặc biệt paid video.
4. Lưu `jobs[].id` ngay khi nhận `202`.
5. Poll `/v1/jobs/status` theo `poll_after_seconds`.
6. Khi complete, tải/lưu media sớm vì URL upstream có thể hết hạn.
7. Khi failed, chỉ retry nếu `retryable=true`; nếu `outcome_unknown=true`, phải đối soát trước.
8. Dùng `/health/ready` cho readiness, không dựa vào `/health/live`.
9. Không tự quản lý Google Flow account/project/media nếu không có nhu cầu compatibility đặc biệt.
10. Không bao giờ chia sẻ `FLOW_PROVIDER_EXTENSION_API_KEY` cho backend client.
