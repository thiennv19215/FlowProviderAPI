# Tài liệu tích hợp FlowProviderAPI

Tài liệu này mô tả contract HTTP hiện tại của FlowProviderAPI dành cho backend tích hợp service-to-service. Provider chuyển request tới Google Flow qua Chrome extension đang đăng nhập và trả lại HTTP status cùng body gần như nguyên bản từ Google Flow.

## 1. Thông tin chung

### Production

```text
Base URL: https://api.shopcongngheso5.io.vn
OpenAPI:  https://api.shopcongngheso5.io.vn/openapi.json
Swagger:  https://api.shopcongngheso5.io.vn/docs
```

### Xác thực

Các endpoint nghiệp vụ là public và không yêu cầu API key:

```http
Content-Type: application/json
```

Có thể gửi thêm `X-Request-Id` để đối soát log. Nếu không gửi, server tự tạo request ID.

### Đặc điểm xử lý

- API lưu mapping project và hash ảnh → media ID theo đúng extension/account/project; đăng nhập Google account khác trên cùng extension không dùng lại mapping cũ.
- **Tự động quản lý project (Auto-managed project)**: Backend tích hợp **không cần tạo hoặc truyền `project_id`**. Provider dùng lại project mới nhất hiện có trên từng tài khoản Google; chỉ tạo project `FlowProvider` khi lookup đầy đủ xác nhận account chưa có project nào. Provider quản lý mapping media/operation. (Nếu backend truyền `project_id`, Provider vẫn tôn trọng để tương thích hoặc nhóm tài nguyên theo ý muốn).
- Ảnh upload được chuyển trực tiếp vào Google Flow dưới dạng Base64.
- Tạo ảnh và video đều trả Provider job (`202 Accepted`). Bên gọi kiểm tra bằng `/v1/jobs/status`.
- Worker gọi Flow một lần cho ảnh; chỉ video cần polling Google Flow. Các endpoint status chỉ đọc DB.
- URL ảnh/video do Google Flow cấp có thể hết hạn hoặc bị thu hồi. Bên tích hợp nên tải và lưu kết quả ngay khi hoàn tất.
- Provider ưu tiên tối đa 3 request đồng thời trên một extension rồi mới chuyển sang extension tiếp theo.
- Mỗi video job reserve trước tối thiểu 20 credits, hoặc mức Omni cao hơn đã biết; các request đồng thời không thể dùng lặp cùng số dư. Sau mọi lần gọi video, kể cả timeout chưa rõ kết quả, Provider khóa paid routing cho account đó đến khi refresh credit thành công. Account vẫn có thể xử lý ảnh.
- Với request video không gửi `X-Provider-Routing-Scope`, nếu project/account được chọn không đủ credit hoặc Flow trả lỗi xác định là hết credit/quota, Provider tự thử một account đủ điều kiện khác tối đa một lần và tự rehydrate các media đã biết sang project mới. Request có routing scope vẫn giữ nguyên account, không tự chuyển.
- Provider lưu account/project và loại poll (`operation` hoặc `media`) của video operation/workflow; `/v1/videos/status` tự chia request theo đúng account, giữ thứ tự đầu vào và gộp kết quả, không cần routing scope đối với operation được tạo qua Provider.

## 2. Danh sách endpoint

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/v1/projects` | Liệt kê Google Flow project của tài khoản. |
| `POST` | `/v1/projects` | Tạo một Google Flow project. |
| `POST` | `/v1/media` | Upload ảnh vào project và nhận media ID. |
| `POST` | `/v1/images/generations` | Tạo ảnh mới, có hoặc không có ảnh tham chiếu. |
| `POST` | `/v1/videos/generations` | Tạo image-to-video hoặc Omni video. |
| `POST` | `/v1/jobs/status` | Đọc trạng thái image/video job từ DB theo `job_ids`. |
| `GET` | `/health/live` | Kiểm tra process API đang chạy. |
| `GET` | `/health/ready` | Kiểm tra extension/tài khoản Flow sẵn sàng. |

## 3. Contract mapping cho backend

Backend tích hợp chỉ gửi các enum ổn định của FlowProviderAPI. Không gửi trực tiếp
Google model key như `GEM_PIX_2`, `NARWHAL`, `veo_3_1_*` hoặc `abra_r2v_*`;
Provider tự map model nội bộ theo loại request, gói account và thời lượng.

### 3.1. Mapping tạo ảnh

| Giá trị nghiệp vụ của backend | `model` gửi Provider | Ghi chú |
|---|---|---|
| `pro` | `pro` | Mặc định; Provider map sang model ảnh Pro hiện hành. |
| `v2` | `v2` | Provider map sang model ảnh v2 hiện hành. |

| Tỷ lệ UI/backend | `aspect_ratio` gửi Provider |
|---|---|
| `1:1`, `square` | `1:1` |
| `16:9`, `landscape` | `16:9` |
| `9:16`, `portrait` | `9:16` |

Nếu bỏ `model`, mặc định là `pro`. Nếu bỏ `aspect_ratio`, mặc định là `9:16`.

### 3.2. Mapping image-to-video

Legacy `type: "image_to_video"` không nhận field `model`. Backend gửi `quality`; Provider kết hợp
`quality + aspect_ratio + paygate tier` của account để chọn Veo model hợp lệ.

| Chế độ backend | `quality` gửi Provider | Ý nghĩa |
|---|---|---|
| `economy`, `lite` | `lite` | Mặc định, ưu tiên tiết kiệm. |
| `fast` | `fast` | Ưu tiên tốc độ. |
| `quality`, `high` | `quality` | Ưu tiên chất lượng. |
| `economy-relaxed` | `lite_relaxed` | Hàng đợi relaxed; chỉ dùng khi account hỗ trợ. |
| `fast-relaxed` | `fast_relaxed` | Fast relaxed; chỉ dùng khi account hỗ trợ. |

| Tỷ lệ UI/backend | `aspect_ratio` gửi Provider |
|---|---|
| `16:9`, `landscape` | `16:9` |
| `9:16`, `portrait` | `9:16` |

Nếu bỏ `quality`, mặc định là `lite`. Nếu bỏ `aspect_ratio`, legacy
`image_to_video` mặc định `16:9`; `i2v` và `omni_i2v` mặc định `9:16` và chọn
model theo `duration_seconds`.

### 3.3. Mapping Omni video

Omni không nhận `model` hoặc `quality`. Backend gửi `duration_seconds`; Provider
tự map sang Omni model tương ứng.

| `duration_seconds` | Model nội bộ Provider tự chọn |
|---:|---|
| `4` | Omni 4 giây |
| `6` | Omni 6 giây |
| `8` | Omni 8 giây, mặc định |
| `10` | Omni 10 giây |

Omni nhận `9:16` hoặc `16:9`; mặc định là `9:16`.

### 3.4. Hàm mapping TypeScript khuyến nghị

```ts
type UiImageModel = "pro" | "v2";
type UiAspect = "1:1" | "16:9" | "9:16";
type UiVideoQuality = "lite" | "fast" | "quality" | "lite_relaxed" | "fast_relaxed";

// Gửi trực tiếp enum nghiệp vụ; Provider tự map sang enum nội bộ của Flow.

export function buildImageRequest(input: {
  prompt: string;
  model?: UiImageModel;
  aspect?: UiAspect;
  variantCount?: number;
  inputImages?: Array<{
    image_base64: string;
    mime_type: string;
    file_name: string;
  }>;
}) {
  return {
    prompt: input.prompt,
    model: IMAGE_MODEL[input.model ?? "pro"],
    aspect_ratio: IMAGE_ASPECT[input.aspect ?? "9:16"],
    variant_count: input.variantCount ?? 1,
    input_images: input.inputImages ?? []
  };
}

export function buildImageToVideoRequest(input: {
  inputImage: { image_base64: string; mime_type: string; file_name: string };
  prompt: string;
  aspect?: Exclude<UiAspect, "1:1">;
  quality?: UiVideoQuality;
}) {
  return {
    type: "image_to_video" as const,
    prompt: input.prompt,
    input_images: [input.inputImage],
    aspect_ratio: VIDEO_ASPECT[input.aspect ?? "16:9"],
    quality: input.quality ?? "lite"
  };
}

export function buildOmniRequest(input: {
  inputImages: Array<{ image_base64: string; mime_type: string; file_name: string }>;
  prompt: string;
  aspect?: Exclude<UiAspect, "1:1">;
  duration?: 4 | 6 | 8 | 10;
}) {
  return {
    type: "omni" as const,
    prompt: input.prompt,
    input_images: input.inputImages,
    aspect_ratio: VIDEO_ASPECT[input.aspect ?? "9:16"],
    duration_seconds: input.duration ?? 8
  };
}
```

Schema dùng `extra="forbid"`. Các field tự chế như `ratio`, `width`, `height`,
`modelKey`, hoặc gửi `model` vào request video sẽ bị trả HTTP `422`. Backend nên
validate enum trước khi gọi Provider và không tự fallback âm thầm sang giá trị khác.

## 4. Luồng tích hợp chuẩn

```text
Luồng chuẩn (Khuyến nghị - Không cần quản lý project):
  1. Gửi thẳng Base64 qua input_images trong request tạo ảnh/video.
  2. Tạo ảnh qua /v1/images/generations hoặc tạo video qua /v1/videos/generations.
  3. Kiểm tra tiến độ video qua /v1/jobs/status.
  -> Provider tự động điều phối account, khởi tạo project, upload/cache media và gộp kết quả.

Chế độ tương thích (Tùy chọn):
  Gọi POST /v1/projects để tự tạo project riêng nếu muốn phân nhóm project theo nghiệp vụ.
```

## 5. Project

### Liệt kê project

```http
GET /v1/projects?page_size=10&cursor=<cursor>
```

`page_size` mặc định là `10`, cho phép từ 1 đến 100. Bỏ `cursor` ở trang đầu; ở trang kế tiếp, gửi cursor do Google Flow trả về. Danh sách nằm tại:

```text
result.data.json.result.projects
```

Khi phân trang, gửi lại `X-Provider-Routing-Scope` nhận từ response để tiếp tục truy vấn đúng tài khoản Google Flow.

### Tạo project

### Request

```http
POST /v1/projects
```

```json
{
  "title": "Campaign August"
}
```

| Field | Bắt buộc | Ràng buộc |
|---|---:|---|
| `title` | Có | Chuỗi từ 1 đến 200 ký tự. |

### Response `200`

Ví dụ response thực tế:

```json
{
  "result": {
    "data": {
      "json": {
        "result": {
          "projectId": "978b0a04-9025-431e-aca8-544f37d0757c",
          "projectInfo": {
            "projectTitle": "Campaign August"
          }
        },
        "status": 200,
        "statusText": "OK"
      }
    }
  }
}
```

Lưu giá trị:

```text
result.data.json.result.projectId
```

Giá trị này được gửi lại dưới field `project_id` ở các request tiếp theo.

## 6. Upload ảnh

Endpoint nhận JSON, không dùng `multipart/form-data`. Bên gọi đọc file, mã hóa Base64 rồi gửi `image_base64`.

Provider băm nội dung ảnh bằng SHA-256. Nếu đúng ảnh đó đã có media ID trong cùng account và project, API trả lại media ID từ DB mà không upload lên Google lần nữa. Có thể kiểm tra header `X-Flow-Media-Cache-Hits`: `1` là cache hit, `0` là upload mới.

Cache hit giữ nguyên HTTP status, body và các upstream header an toàn của lần upload đầu.

### Request

```http
POST /v1/media
```

```json
{
  "file_name": "product.jpg",
  "mime_type": "image/jpeg",
  "image_base64": "/9j/4AAQSkZJRgABAQ..."
}
```

| Field | Bắt buộc | Ràng buộc |
|---|---:|---|
| `project_id` | Không | Bỏ qua để Provider tự dùng managed project mặc định, hoặc truyền nếu muốn chỉ định project cụ thể. |
| `required_credits` | Không | Mặc định `0`. Khi upload ảnh để tạo video, truyền số credit tối thiểu của video (`20` cho I2V/lite; Omni 8 giây là `25`) để ảnh được đặt trên một account đủ khả năng render. |
| `excluded_project_ids` | Không | Danh sách project của các account đã thất bại trong cùng lượt failover. Provider loại các account sở hữu project này và upload lại ảnh sang account khác. |
| `file_name` | Không | Mặc định `upload.png`, tối đa 255 ký tự. |
| `mime_type` | Có | Bắt đầu bằng `image/`, ví dụ `image/jpeg`, `image/png`. |
| `image_base64` | Có | Chuỗi Base64 thuần, không thêm prefix `data:image/...;base64,`. Tổng Base64 trong một request tối đa 64 MiB ký tự. |

### Response `200`

```json
{
  "media": {
    "name": "d3fc46fb-ed33-4bf2-bd25-1bf88e3541dd",
    "projectId": "978b0a04-9025-431e-aca8-544f37d0757c",
    "workflowId": "55e945e4-ebf9-42c2-b1ad-b26063f5bcfa",
    "mediaMetadata": {
      "createTime": "2026-08-13T15:10:35.854022Z",
      "visibility": "PRIVATE",
      "mediaBlobSize": "73680"
    },
    "image": {
      "dimensions": {
        "width": 768,
        "height": 1376
      }
    }
  },
  "workflow": {
    "name": "55e945e4-ebf9-42c2-b1ad-b26063f5bcfa",
    "metadata": {
      "displayName": "product.jpg",
      "primaryMediaId": "d3fc46fb-ed33-4bf2-bd25-1bf88e3541dd"
    },
    "projectId": "978b0a04-9025-431e-aca8-544f37d0757c"
  }
}
```

Media ID chuẩn cần lưu là:

```text
media.name
```

`workflow.metadata.primaryMediaId` thường có cùng giá trị. Dùng media ID này trong `reference_media_ids` khi tạo ảnh/Omni video hoặc `start_media_id` khi tạo image-to-video.

### Ví dụ Node.js upload file

```js
import { readFile } from "node:fs/promises";

const API_URL = "https://api.shopcongngheso5.io.vn";
const bytes = await readFile("./product.jpg");

const response = await fetch(`${API_URL}/v1/media`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    project_id: "978b0a04-9025-431e-aca8-544f37d0757c",
    file_name: "product.jpg",
    mime_type: "image/jpeg",
    image_base64: bytes.toString("base64")
  })
});

const body = await response.json();
if (!response.ok) throw new Error(JSON.stringify(body));
const mediaId = body.media.name;
```

## 7. Tạo ảnh

### Chế độ tự động (khuyến nghị)

Không gửi `project_id`. Provider tự chọn extension ít tải, tạo hoặc tái sử dụng project của account đó và upload các ảnh trong `input_images` trước khi generate:

```json
{
  "prompt": "Preserve this product and create a luxury advertising scene",
  "input_images": [
    {
      "image_base64": "<base64>",
      "mime_type": "image/jpeg",
      "file_name": "product.jpg"
    }
  ],
  "model": "pro",
  "aspect_ratio": "9:16",
  "variant_count": 1
}
```

Response `202` có `X-Flow-Project-Id` và `jobs[0].id`. Dùng job ID với `/v1/jobs/status`; khi `complete`, lấy `jobs[0].media[].id` để làm reference hoặc tạo video.

Với luồng tách riêng `POST /v1/media` rồi `POST /v1/videos/generations`, caller phải gửi `required_credits` khi upload và chuyển `X-Flow-Project-Id` từ response upload thành `project_id` của request video. Điều này giữ cả hai call trên cùng Chrome extension/account trong triển khai đa account.

Nếu cùng nội dung ảnh đã được upload vào đúng account/project, Provider dùng thẳng media ID trong DB. Nếu worker gặp `404` do cache cũ, job kết thúc `failed` và cache bị vô hiệu hóa; request mới sau đó sẽ upload lại ảnh. Header `X-Flow-Media-Cache-Hits` cho biết số ảnh lấy từ cache khi xếp hàng.

### Request không có ảnh tham chiếu

```http
POST /v1/images/generations
```

```json
{
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "prompt": "A premium product photograph, soft studio lighting, no text",
  "model": "pro",
  "aspect_ratio": "9:16",
  "reference_media_ids": [],
  "variant_count": 1
}
```

### Request dùng ảnh tham chiếu

```json
{
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "prompt": "Preserve the referenced product identity and create a luxury advertising scene",
  "model": "pro",
  "aspect_ratio": "9:16",
  "reference_media_ids": [
    "d3fc46fb-ed33-4bf2-bd25-1bf88e3541dd"
  ],
  "variant_count": 1
}
```

Ảnh vừa sinh cũng có media ID và có thể tiếp tục làm tham chiếu cho lần tạo tiếp theo. Với routing scope, media vẫn phải thuộc đúng project/account. Nếu chỉ truyền `project_id` hoặc bỏ cả hai field route, media đã biết từ account khác có thể được tự rehydrate sang project/account được chọn; gọi lại cùng nội dung không bị báo conflict.

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `project_id` | Không | Chỉ dùng cho chế độ tương thích; bỏ qua để Provider tự quản lý project. |
| `prompt` | Có | 1–12.000 ký tự. |
| `model` | Không | `pro` (mặc định), `v2`. Enum dài cũ vẫn được nhận trong giai đoạn chuyển tiếp. |
| `aspect_ratio` | Không | `9:16` (mặc định), `16:9`, `1:1`. Enum dài cũ vẫn được nhận trong giai đoạn chuyển tiếp. |
| `reference_media_ids` | Không | Tối đa 8 media ID trong cùng project. |
| `input_images` | Không | Tối đa 8 ảnh Base64 để Provider tự upload; tổng cùng `reference_media_ids` không quá 8. |
| `variant_count` | Không | Từ 1 đến 4, mặc định 1. |

### Response tạo job `202`

Response tạo ảnh chỉ xác nhận job đã được lưu:

```json
{
  "jobs": [{
    "id": "job_abc123",
    "type": "image",
    "status": "queued",
    "media": [],
    "error": null
  }],
  "metadata": {
    "request_id": "req_123",
    "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
    "routing_scope": "opaque-token",
    "poll_after_seconds": 5,
    "counts": {"queued": 1, "running": 0, "complete": 0, "failed": 0},
    "done": false
  }
}
```

Khi status là `complete`, media ID nằm ở `jobs[i].media[].id` và URL ở `jobs[i].media[].url`.

## 8. Tạo video từ khung hình (i2v / Image-to-Video)

`i2v` và `omni_i2v` sử dụng Gemini Omni Flash (`abra_i2v_*`), chọn model theo `duration_seconds` và mặc định khung hình dọc (`9:16`). Legacy `image_to_video` sử dụng Veo, chọn model theo `quality` và mặc định khung hình ngang (`16:9`). Hỗ trợ cả tùy chọn khung hình cuối (`end_media_id`) để nối cảnh liền mạch.

### Request

```http
POST /v1/videos/generations
```

```json
{
  "type": "i2v",
  "prompt": "Slow cinematic camera movement around the product",
  "start_media_id": "media-id-khung-dau",
  "end_media_id": "media-id-khung-cuoi-tuy-chon",
  "aspect_ratio": "9:16",
  "duration_seconds": 8
}
```

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `type` | Có | `i2v` (khuyên dùng), `omni_i2v`, hoặc chế độ legacy Veo `image_to_video`. |
| `project_id` | Không | Bỏ qua để Provider tự dùng managed project mặc định, hoặc truyền nếu muốn chỉ định project. |
| `prompt` | Có | 1–12.000 ký tự mô tả chuyển động. |
| `start_media_id` | Có | ID ảnh làm khung hình xuất phát (hoặc 1 ảnh trong `input_images`). |
| `end_media_id` | Không | ID ảnh làm khung hình kết thúc (tùy chọn: dùng để chuyển cảnh First+Last frame mượt mà). |
| `duration_seconds` | Không | `4`, `6`, `8` (mặc định), `10` giây. |
| `aspect_ratio` | Không | `9:16` hoặc `16:9`. Mặc định `9:16` cho `i2v`/`omni_i2v`; `16:9` cho legacy `image_to_video`. |

## 9. Tạo video từ ảnh tham chiếu (r2v / Reference-to-Video)

Sử dụng Gemini Omni Flash (`abra_r2v_*`) nhận diện nhân vật / phong cách từ 1 đến 8 ảnh tham chiếu để tạo video mới.

### Request

```json
{
  "type": "r2v",
  "prompt": "Create a vertical cinematic product advertisement",
  "reference_media_ids": ["media-id-1", "media-id-2"],
  "aspect_ratio": "9:16",
  "duration_seconds": 8
}
```

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `type` | Có | `r2v` (khuyên dùng), `omni_r2v`, hoặc alias cũ `omni`. |
| `project_id` | Không | Bỏ qua để Provider tự dùng managed project mặc định. |
| `prompt` | Có | 1–12.000 ký tự. |
| `reference_media_ids` | Có | Danh sách 1 đến 8 ID ảnh tham chiếu (hoặc truyền qua `input_images`). |
| `duration_seconds` | Không | `4`, `6`, `8` (mặc định), `10` giây. |
| `aspect_ratio` | Không | `9:16` (mặc định) hoặc `16:9`. |

### Response bắt đầu tạo video

Provider lưu request vào database trước khi background worker gửi paid request tới Flow. Response trả Provider job ID ổn định:

Nên gửi header `Idempotency-Key` không rỗng (tối đa 200 ký tự) cho mỗi yêu cầu
nghiệp vụ. Gọi lại cùng key và cùng payload trả lại chính job đã lưu với HTTP
`202`; dùng cùng key cho payload khác trả `409 IDEMPOTENCY_KEY_REUSED`.

```json
{
  "jobs": [{
    "id": "job_abc123",
    "type": "video",
    "status": "queued",
    "media": [],
    "error": null
  }],
  "metadata": {
    "request_id": "req_123",
    "project_id": "projects/123",
    "poll_after_seconds": 5,
    "counts": {"queued": 1, "running": 0, "complete": 0, "failed": 0},
    "done": false
  }
}
```

## 10. Kiểm tra trạng thái job

### Request

```http
POST /v1/jobs/status
```

```json
{
  "job_ids": [
    "job_abc123"
  ]
}
```

`job_ids` nhận từ 1 đến 20 phần tử. Endpoint chỉ đọc database, không cần extension
đang online và không trả routing scope của account. Hãy coi Provider job ID là
opaque identifier và không dùng operation/workflow/media ID của Flow để polling.
`metadata.counts` tổng hợp số job theo bốn trạng thái và `metadata.done` chỉ bằng
`true` khi không còn job `queued` hoặc `running`.

### Response đang xử lý

```json
{
  "jobs": [{
    "id": "job_abc123",
    "type": "video",
    "status": "running",
    "media": [],
    "error": null
  }],
  "metadata": {
    "request_id": "req_456",
    "project_id": "projects/123",
    "poll_after_seconds": 5,
    "counts": {"queued": 0, "running": 1, "complete": 0, "failed": 0},
    "done": false
  }
}
```

### Response hoàn tất

Worker đổi media ID thành signed URL và lưu kết quả vào database trước khi chuyển job sang `complete`:

```json
{
  "jobs": [{
    "id": "job_abc123",
    "type": "video",
    "status": "complete",
    "media": [{
      "id": "VIDEO_MEDIA_ID",
      "type": "video",
      "url": "https://flow-content.google/video/...",
      "thumbnail_url": "https://flow-content.google/thumbnail/...",
      "width": 1080,
      "height": 1920,
      "duration_seconds": 8
    }],
    "error": null
  }],
  "metadata": {
    "request_id": "req_789",
    "project_id": "projects/123",
    "poll_after_seconds": null,
    "counts": {"queued": 0, "running": 0, "complete": 1, "failed": 0},
    "done": true
  }
}
```

Trạng thái public cố định: `queued`, `running`, `complete`, `failed`. Khi `failed`, đọc lỗi tại `jobs[i].error`. Không tạo lại mù quáng vì paid request có thể đã được Flow nhận.

### Khuyến nghị polling

- Poll mỗi 5–10 giây; không gọi liên tục.
- Dừng khi mọi job có trạng thái `complete` hoặc `failed`.
- Đặt timeout nghiệp vụ phù hợp, ví dụ 10 phút.
- HTTP `200` của endpoint status chỉ có nghĩa request kiểm tra hợp lệ; vẫn phải đọc `jobs[].status`.
- API không trả header `Retry-After`; bên gọi tự quản lý nhịp polling.

## 11. HTTP status và lỗi

Backend phải xử lý ba nhóm lỗi độc lập:

1. Provider từ chối request và trả error envelope chuẩn.
2. Với các endpoint đồng bộ, Google Flow có thể trả HTTP lỗi và Provider chuyển đổi/chuyển tiếp lỗi đó.
3. Với video async, worker lưu lỗi terminal vào DB; `/v1/videos/status` vẫn trả HTTP `200` và lỗi có cấu trúc tại `jobs[].error`.

Các mã terminal video gồm `VIDEO_DISPATCH_FAILED`,
`VIDEO_DISPATCH_OUTCOME_UNKNOWN`, `VIDEO_OPERATION_FAILED`,
`VIDEO_MEDIA_FAILED` và `VIDEO_POLL_TIMEOUT`. Nếu `outcome_unknown: true`, phải
reconcile paid request cũ và không tự động tạo request thay thế.

### 11.1. Lỗi do Provider phát sinh

Lỗi do lớp API phát sinh dùng envelope chuẩn:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {
        "field": "variant_count",
        "code": "OUT_OF_RANGE",
        "message": "Input should be less than or equal to 4"
      }
    ],
    "request_id": "req_4c0ae693a14c4ec7831b3037803bf132",
    "retryable": false
  }
}
```

Ý nghĩa các field:

| Field | Cách xử lý |
|---|---|
| `error.status_code` | Giống HTTP status của response. |
| `error.code` | Mã ổn định để backend điều khiển nghiệp vụ. |
| `error.details[]` | Lỗi theo field; có thể rỗng. |
| `error.request_id` | Dùng đối soát log với Provider. |
| `error.retryable` | `true` khi cùng request có thể thử lại sau backoff. |

Validation có thể trả các detail code: `REQUIRED_FIELD`, `INVALID_CHOICE`,
`UNKNOWN_FIELD`, `INVALID_LENGTH`, `OUT_OF_RANGE`, `INVALID_TYPE`,
`INVALID_VALUE`. Backend nên hiển thị lỗi theo `details[i].field`, không parse
chuỗi tiếng Anh trong `message`.

### 11.2. Lỗi chuyển tiếp từ Google Flow

Khi Google Flow đã phản hồi HTTP, Provider giữ status và body upstream. Response
có header `X-Flow-Upstream-Status`, ví dụ:

```http
HTTP/1.1 429 Too Many Requests
X-Flow-Upstream-Status: 429
X-Request-Id: req_...
Content-Type: application/json
```

Body upstream không bắt buộc có envelope `{"error":{"code":...}}` của Provider;
nó có thể là JSON shape khác hoặc plain text. Backend nhận biết nhóm này bằng
header `X-Flow-Upstream-Status`, lưu `X-Request-Id`, và tạo mã nội bộ như
`FLOW_UPSTREAM_429` nếu cần chuẩn hóa.

### 11.3. Video thất bại bên trong HTTP `200`

`POST /v1/videos/status` có thể trả HTTP `200` nhưng task thất bại. Với operation,
kiểm tra `operation.error`. Với media polling, kiểm tra
`media[i].mediaMetadata.mediaStatus.mediaGenerationStatus`; mọi trạng thái terminal
khác `MEDIA_GENERATION_STATUS_SUCCESSFUL` phải được coi là thất bại, ví dụ:

```json
{
  "media": [
    {
      "name": "VIDEO_MEDIA_ID",
      "mediaMetadata": {
        "mediaStatus": {
          "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_UNSUCCESSFUL"
        }
      }
    }
  ]
}
```

Không tự tạo lại video chỉ dựa trên lỗi poll. Request generation ban đầu có thể đã
trừ credit; backend nên lưu poll name, trạng thái cuối và `X-Request-Id` để đối soát.

| HTTP | Code thường gặp | Ý nghĩa/Xử lý |
|---:|---|---|
| `400` | `INVALID_JSON`, `INVALID_CONTENT_LENGTH`, `ROUTING_SCOPE_INVALID` | Sửa request/header, không retry nguyên trạng. |
| `403` | Upstream Flow | Tài khoản/quyền/captcha bị từ chối; trả lỗi cho người dùng. |
| `404` | `ENDPOINT_NOT_FOUND` hoặc upstream | Sai endpoint/resource/media/operation. |
| `409` | `PROJECT_ROUTE_UNKNOWN`, `OPERATION_ROUTE_UNKNOWN` | Provider chưa biết account sở hữu project/operation; list project hoặc dùng identifier do Provider vừa trả. |
| `409` | `PROJECT_ACCOUNT_MISMATCH`, `OPERATION_ACCOUNT_MISMATCH` | Routing scope không sở hữu resource; không chuyển sang account khác. |
| `413` | `PAYLOAD_TOO_LARGE` | Ảnh Base64 hoặc request quá lớn. |
| `422` | `VALIDATION_ERROR`, `INVALID_IMAGE_BASE64`, `INVALID_VIDEO_QUALITY` | Field/payload/model không hợp lệ; sửa request. |
| `429` | Upstream Flow hoặc rate limit API | Hết quota/bị giới hạn; backoff trước khi thử lại. |
| `500` | `INTERNAL_ERROR` | Lỗi Provider ngoài dự kiến; retry có giới hạn và báo vận hành. |
| `502` | `EXTENSION_REQUEST_FAILED`, `PROJECT_RECOVERY_FAILED` | Extension/Flow trả response không hợp lệ hoặc khôi phục project thất bại. |
| `503` | `PROVIDER_ACCOUNT_UNAVAILABLE`, `PROJECT_ACCOUNT_UNAVAILABLE`, `VIDEO_ACCOUNT_UNAVAILABLE` | Không có account/slot/credit phù hợp; retry với backoff. |
| `503` | `ROUTING_SCOPE_UNAVAILABLE`, `OPERATION_ACCOUNT_UNAVAILABLE`, `EXTENSION_DISCONNECTED` | Account sở hữu resource đang offline hoặc hết slot; không fallback sang account khác. |
| `504` | `EXTENSION_TIMEOUT` | Hết thời gian chờ. Với video trả phí, `retryable=false` vì kết quả có thể đã được Flow nhận; không tạo lại khi chưa đối soát operation gốc. |

Luôn ưu tiên xử lý theo `HTTP status`, sau đó theo `error.code` và `retryable`;
không phân tích chuỗi `message` để điều khiển nghiệp vụ.

## 12. Hàm gọi API mẫu

```js
const API_URL = "https://api.shopcongngheso5.io.vn";
async function callFlow(path, payload) {
  const requestId = crypto.randomUUID();
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Request-Id": requestId
    },
    body: JSON.stringify(payload)
  });

  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const providerError = typeof body === "object" ? body?.error : null;
    const upstreamStatus = response.headers.get("x-flow-upstream-status");
    const error = new Error(
      providerError?.message ?? `FlowProvider returned ${response.status}`
    );
    error.status = response.status;
    error.code = providerError?.code
      ?? (upstreamStatus ? `FLOW_UPSTREAM_${upstreamStatus}` : `HTTP_${response.status}`);
    error.retryable = providerError?.retryable
      ?? [408, 425, 429, 500, 502, 503, 504].includes(response.status);
    error.details = providerError?.details ?? [];
    error.requestId = response.headers.get("x-request-id");
    error.body = body;
    throw error;
  }

  return body;
}

export function assertVideoPollSucceeded(body) {
  for (const item of body.operations ?? []) {
    const operation = item?.operation ?? item;
    if (operation?.error) {
      const error = new Error("Google Flow video operation failed");
      error.code = "VIDEO_OPERATION_FAILED";
      error.body = operation.error;
      throw error;
    }
  }

  const failedMediaStatuses = new Set([
    "MEDIA_GENERATION_STATUS_UNSUCCESSFUL",
    "MEDIA_GENERATION_STATUS_FAILED",
    "MEDIA_GENERATION_STATUS_CANCELLED"
  ]);
  for (const media of body.media ?? []) {
    const status = media?.mediaMetadata?.mediaStatus?.mediaGenerationStatus;
    if (failedMediaStatuses.has(status)) {
      const error = new Error(`Google Flow video failed with ${status}`);
      error.code = "VIDEO_MEDIA_FAILED";
      error.body = media;
      throw error;
    }
  }
}
```

## 13. Health check

Health endpoint không yêu cầu API key:

```http
GET /health/live
GET /health/ready
```

Ví dụ `/health/ready`:

```json
{
  "status": "ready",
  "provider_accounts": 2,
  "video_lite_ready_accounts": 2
}
```

`status` là `waiting_for_provider` khi API/SQLite đã hoạt động nhưng chưa có extension sẵn sàng. Trạng thái này vẫn trả HTTP 200 để extension có thể kết nối. Khi SQLite không truy cập được, endpoint trả HTTP 503 với `status: unavailable`. Chỉ gửi request tạo nội dung khi `status` là `ready` và `provider_accounts` lớn hơn `0`.

## 14. Checklist cho bên tích hợp

- Lưu API key ở backend/secret manager.
- Backend **không cần tạo hay quản lý `project_id`**; Provider sẽ tự động điều phối và gom nhóm tài nguyên.
- Sau upload hoặc tạo ảnh, lưu `media.name` làm media ID để dùng cho các bước tiếp theo (video/reference).
- Sau tạo video, lưu `jobs[].id`, rồi đọc trạng thái có khoảng nghỉ qua `/v1/videos/status` bằng `job_ids`.
- Lưu kết quả ảnh/video về storage của hệ thống tích hợp trước khi URL Flow hết hạn.
- Ghi log `X-Request-Id`, HTTP status và `error.code`; không ghi API key hoặc URL signed đầy đủ vào log công khai.
