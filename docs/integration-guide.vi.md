# Tài liệu tích hợp FlowProviderAPI

FlowProviderAPI gọi Google Flow thông qua Chrome extension đã đăng nhập. Bên tích hợp chỉ gọi API HTTP này; không cần biết URL nội bộ của Google Flow, browser cookie, bearer token hoặc captcha.

API không tạo job riêng, không lưu file và không tự polling video. Mỗi request được chuyển tới extension; khi Google Flow phản hồi, API trả lại HTTP status và body upstream đó cho client.

## Base URL và xác thực

Base URL production do đơn vị vận hành cung cấp:

```text
https://<provider-domain>
```

Mọi business endpoint yêu cầu các header sau:

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

`X-Request-Id` là tùy chọn. Có thể gửi ID của hệ thống gọi để trace; mọi response đều trả header này.

## API surface

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/v1/projects` | Tạo Google Flow project. |
| `POST` | `/v1/media` | Upload một ảnh base64 vào project. |
| `POST` | `/v1/images/generations` | Tạo một đến bốn ảnh. |
| `POST` | `/v1/videos/generations` | Tạo video từ ảnh hoặc Omni video. |
| `POST` | `/v1/videos/status` | Kiểm tra một hoặc nhiều video operation. |

## Quy trình gọi cơ bản

1. Tạo project bằng `/v1/projects` và lấy `projectId` từ response Google Flow.
2. Nếu có ảnh tham chiếu, upload qua `/v1/media`, rồi lấy media ID từ response.
3. Tạo ảnh bằng `/v1/images/generations`, hoặc tạo video bằng `/v1/videos/generations`.
4. Video trả về operation name. Gửi name đó đến `/v1/videos/status` cho tới khi response Google Flow báo hoàn tất hoặc lỗi.

## 1. Tạo project

```http
POST /v1/projects
```

```json
{
  "title": "Campaign August"
}
```

`title` bắt buộc, dài 1–200 ký tự.

## 2. Upload ảnh

```http
POST /v1/media
```

```json
{
  "project_id": "<PROJECT_ID>",
  "file_name": "product.png",
  "mime_type": "image/png",
  "image_base64": "<BASE64_ENCODED_IMAGE>"
}
```

| Field | Bắt buộc | Ràng buộc |
|---|---:|---|
| `project_id` | Có | ID lấy từ response tạo project. |
| `file_name` | Không | Mặc định `upload.png`. |
| `mime_type` | Có | Phải bắt đầu bằng `image/`. |
| `image_base64` | Có | Nội dung ảnh mã hóa Base64, tối đa 64 MiB ký tự. |

Lưu media ID Google Flow trả về để làm `reference_media_ids` hoặc `start_media_id` trong request sau.

## 3. Tạo ảnh

```http
POST /v1/images/generations
```

```json
{
  "project_id": "<PROJECT_ID>",
  "prompt": "A premium product photograph, soft studio lighting",
  "model": "NANO_BANANA_PRO",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_SQUARE",
  "reference_media_ids": ["<MEDIA_ID>"],
  "variant_count": 1
}
```

| Field | Bắt buộc | Giá trị |
|---|---:|---|
| `project_id` | Có | Google Flow project ID. |
| `prompt` | Có | 1–12.000 ký tự. |
| `model` | Không | `NANO_BANANA_PRO` (mặc định) hoặc `NANO_BANANA_2`. |
| `aspect_ratio` | Không | `IMAGE_ASPECT_RATIO_SQUARE`, `IMAGE_ASPECT_RATIO_LANDSCAPE`, `IMAGE_ASPECT_RATIO_PORTRAIT` (mặc định). |
| `reference_media_ids` | Không | 0–8 media ID đã upload. |
| `variant_count` | Không | 1–4, mặc định 1. |

## 4. Tạo video

```http
POST /v1/videos/generations
```

`type` quyết định dạng video.

### Image-to-video

```json
{
  "type": "image_to_video",
  "project_id": "<PROJECT_ID>",
  "prompt": "Slow cinematic camera movement around the product",
  "start_media_id": "<MEDIA_ID>",
  "aspect_ratio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "quality": "lite"
}
```

`quality`: `lite` (mặc định), `fast`, `quality`, `lite_relaxed`, hoặc `fast_relaxed`. Một số quality phụ thuộc loại tài khoản Google Flow.

### Omni video

```json
{
  "type": "omni",
  "project_id": "<PROJECT_ID>",
  "prompt": "A vertical product advertisement with dynamic movement",
  "reference_media_ids": ["<MEDIA_ID_1>", "<MEDIA_ID_2>"],
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
  "duration_seconds": 8
}
```

`reference_media_ids` nhận 1–8 media ID. `duration_seconds` nhận `2`, `4`, `8` (mặc định) hoặc `10`.

## 5. Kiểm tra trạng thái video

```http
POST /v1/videos/status
```

```json
{
  "operation_names": [
    "operations/123"
  ]
}
```

Gửi một đến 20 operation name. API trả nguyên response status của Google Flow; client tự quyết định khoảng polling và khi nào coi video hoàn tất.

## Response và lỗi

Khi request đã tới Google Flow, API giữ nguyên HTTP status/body upstream. Response có thêm:

```http
X-Flow-Upstream-Status: 200
X-Request-Id: req_...
```

Các lỗi phát sinh trước khi có response từ Google Flow dùng envelope chung:

```json
{
  "error": {
    "status_code": 503,
    "code": "PROVIDER_ACCOUNT_UNAVAILABLE",
    "message": "No Google Flow extension is currently available.",
    "details": [],
    "request_id": "req_...",
    "retryable": true
  }
}
```

| HTTP | Code | Xử lý |
|---:|---|---|
| 400 | `INVALID_JSON` | Sửa JSON request. |
| 401 | `INVALID_API_KEY` | Kiểm tra API key. |
| 422 | `VALIDATION_ERROR` | Sửa field hoặc giá trị request. |
| 503 | `PROVIDER_ACCOUNT_UNAVAILABLE` | Extension/tài khoản chưa sẵn sàng; retry có backoff. |
| 503 | `EXTENSION_DISCONNECTED` | Extension mất kết nối; retry có backoff. |
| 504 | `EXTENSION_TIMEOUT` | Hết thời gian chờ; kiểm tra trạng thái video trước khi gọi lại. |

## Health check

```http
GET /health/live
GET /health/ready
```

`/health/ready` trả số tài khoản Google Flow đang sẵn sàng. OpenAPI tương tác có tại `GET /docs`; schema JSON có tại `GET /openapi.json`.
