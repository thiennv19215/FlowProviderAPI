# Tài liệu tích hợp FlowProviderAPI

Tài liệu này mô tả contract HTTP hiện tại của FlowProviderAPI dành cho backend/frontend tích hợp. API chuyển request tới Google Flow qua Chrome extension đang đăng nhập và trả lại HTTP status cùng body gần như nguyên bản từ Google Flow.

## 1. Thông tin chung

### Production

```text
Base URL: https://api.shopcongngheso5.io.vn
OpenAPI:  https://api.shopcongngheso5.io.vn/openapi.json
Swagger:  https://api.shopcongngheso5.io.vn/docs
```

### Xác thực

Mọi endpoint nghiệp vụ yêu cầu API key:

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

Có thể gửi thêm `X-Request-Id` để đối soát log. Nếu không gửi, server tự tạo request ID. Không đưa API key vào query string hoặc mã frontend công khai.

### Đặc điểm xử lý

- API lưu mapping project và hash ảnh → media ID theo đúng extension/account/project; đăng nhập Google account khác trên cùng extension không dùng lại mapping cũ.
- Ảnh upload được chuyển trực tiếp vào Google Flow dưới dạng Base64.
- Tạo ảnh trả kết quả đồng bộ sau khi Flow xử lý xong.
- Tạo video trả operation; bên gọi chủ động kiểm tra bằng `/v1/videos/status`.
- URL ảnh/video do Google Flow cấp có thể hết hạn hoặc bị thu hồi. Bên tích hợp nên tải và lưu kết quả ngay khi hoàn tất.
- Các media dùng chung một luồng phải thuộc cùng `project_id`.
- Provider ưu tiên tối đa 3 request đồng thời trên một extension rồi mới chuyển sang extension tiếp theo.
- Mỗi video job reserve trước tối thiểu 20 credits, hoặc mức Omni cao hơn đã biết; các request đồng thời không thể dùng lặp cùng số dư. Sau mọi lần gọi video, kể cả timeout chưa rõ kết quả, Provider khóa paid routing cho account đó đến khi refresh credit thành công. Account vẫn có thể xử lý ảnh.
- Provider lưu account/project và loại poll (`operation` hoặc `media`) của video operation/workflow; `/v1/videos/status` tự chia request theo đúng account, giữ thứ tự đầu vào và gộp kết quả, không cần routing scope đối với operation được tạo qua Provider.

## 2. Danh sách endpoint

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/v1/projects` | Liệt kê Google Flow project của tài khoản. |
| `POST` | `/v1/projects` | Tạo một Google Flow project. |
| `POST` | `/v1/media` | Upload ảnh vào project và nhận media ID. |
| `POST` | `/v1/images/generations` | Tạo ảnh mới, có hoặc không có ảnh tham chiếu. |
| `POST` | `/v1/videos/generations` | Tạo image-to-video hoặc Omni video. |
| `POST` | `/v1/videos/status` | Kiểm tra trạng thái các video operation. |
| `GET` | `/health/live` | Kiểm tra process API đang chạy. |
| `GET` | `/health/ready` | Kiểm tra extension/tài khoản Flow sẵn sàng. |

## 3. Luồng tích hợp chuẩn

```text
Tạo ảnh tự động:
  gửi prompt + input_images
  -> Provider chọn account, project, upload và generate
  -> nhận kết quả

Chế độ tương thích/video:
  tạo project -> upload -> generate -> kiểm tra status
```

Một project có thể được dùng xuyên suốt nhiều lần tạo ảnh/video. Không cần tạo project mới cho từng request.

## 4. Project

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

## 5. Upload ảnh

Endpoint nhận JSON, không dùng `multipart/form-data`. Bên gọi đọc file, mã hóa Base64 rồi gửi `image_base64`.

Provider băm nội dung ảnh bằng SHA-256. Nếu đúng ảnh đó đã có media ID trong cùng account và project, API trả lại media ID từ DB mà không upload lên Google lần nữa. Có thể kiểm tra header `X-Flow-Media-Cache-Hits`: `1` là cache hit, `0` là upload mới.

Cache hit giữ nguyên HTTP status, body và các upstream header an toàn của lần upload đầu.

### Request

```http
POST /v1/media
```

```json
{
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "file_name": "product.jpg",
  "mime_type": "image/jpeg",
  "image_base64": "/9j/4AAQSkZJRgABAQ..."
}
```

| Field | Bắt buộc | Ràng buộc |
|---|---:|---|
| `project_id` | Có | Project ID của Google Flow. |
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
const API_KEY = process.env.FLOW_PROVIDER_API_KEY;
const bytes = await readFile("./product.jpg");

const response = await fetch(`${API_URL}/v1/media`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${API_KEY}`,
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

## 6. Tạo ảnh

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
  "model": "NANO_BANANA_PRO",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
  "variant_count": 1
}
```

Response có `X-Flow-Project-Id` để đối soát, nhưng backend tích hợp không cần lưu hoặc điều hướng project này.

Nếu cùng nội dung ảnh đã được upload vào đúng account/project, Provider dùng thẳng media ID trong DB mà không thêm request kiểm tra. Nếu Google hiếm khi trả `404` vì media cũ, Provider xóa cache, upload lại và retry. Header `X-Flow-Media-Cache-Hits` cho biết số ảnh lấy từ cache trong request.

### Request không có ảnh tham chiếu

```http
POST /v1/images/generations
```

```json
{
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "prompt": "A premium product photograph, soft studio lighting, no text",
  "model": "NANO_BANANA_PRO",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
  "reference_media_ids": [],
  "variant_count": 1
}
```

### Request dùng ảnh tham chiếu

```json
{
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "prompt": "Preserve the referenced product identity and create a luxury advertising scene",
  "model": "NANO_BANANA_PRO",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
  "reference_media_ids": [
    "d3fc46fb-ed33-4bf2-bd25-1bf88e3541dd"
  ],
  "variant_count": 1
}
```

Ảnh vừa sinh cũng có media ID và có thể tiếp tục làm tham chiếu cho lần tạo tiếp theo, miễn là vẫn dùng cùng project.

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `project_id` | Không | Chỉ dùng cho chế độ tương thích; bỏ qua để Provider tự quản lý project. |
| `prompt` | Có | 1–12.000 ký tự. |
| `model` | Không | `NANO_BANANA_PRO` (mặc định), `NANO_BANANA_2`. |
| `aspect_ratio` | Không | `IMAGE_ASPECT_RATIO_PORTRAIT` (mặc định), `IMAGE_ASPECT_RATIO_LANDSCAPE`, `IMAGE_ASPECT_RATIO_SQUARE`. |
| `reference_media_ids` | Không | Tối đa 8 media ID trong cùng project. |
| `input_images` | Không | Tối đa 8 ảnh Base64 để Provider tự upload; tổng cùng `reference_media_ids` không quá 8. |
| `variant_count` | Không | Từ 1 đến 4, mặc định 1. |

### Response `200`

Ví dụ đã rút gọn từ response thực tế:

```json
{
  "media": [
    {
      "name": "b207ef8f-e3ce-44d3-9f2f-043dd0a61275",
      "workflowId": "e22a35f4-518d-4104-b15b-0ed725a8e394",
      "image": {
        "generatedImage": {
          "mediaId": "b207ef8f-e3ce-44d3-9f2f-043dd0a61275",
          "modelNameType": "GEM_PIX_2",
          "fifeUrl": "https://flow-content.google/image/...",
          "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
          "requestData": {
            "imageGenerationRequestData": {
              "imageGenerationImageInputs": [
                {
                  "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
                  "mediaId": "d3fc46fb-ed33-4bf2-bd25-1bf88e3541dd"
                }
              ]
            }
          }
        },
        "dimensions": {
          "width": 768,
          "height": 1376
        }
      }
    }
  ],
  "workflows": [
    {
      "name": "e22a35f4-518d-4104-b15b-0ed725a8e394",
      "metadata": {
        "primaryMediaId": "b207ef8f-e3ce-44d3-9f2f-043dd0a61275"
      },
      "projectId": "978b0a04-9025-431e-aca8-544f37d0757c"
    }
  ]
}
```

Với mỗi phần tử trong `media[]`, lấy:

| Dữ liệu | JSON path |
|---|---|
| Media ID kết quả | `media[i].name` hoặc `media[i].image.generatedImage.mediaId` |
| URL ảnh | `media[i].image.generatedImage.fifeUrl` |
| Chiều rộng | `media[i].image.dimensions.width` |
| Chiều cao | `media[i].image.dimensions.height` |

Số phần tử `media[]` tương ứng với số biến thể Flow trả về. Không giả định luôn chỉ có một ảnh.

## 7. Tạo image-to-video

### Request

```http
POST /v1/videos/generations
```

```json
{
  "type": "image_to_video",
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "prompt": "Slow cinematic camera movement around the product",
  "start_media_id": "b207ef8f-e3ce-44d3-9f2f-043dd0a61275",
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
  "quality": "lite"
}
```

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `type` | Có | Luôn là `image_to_video`. |
| `project_id` | Có | Project chứa `start_media_id`. |
| `prompt` | Có | 1–12.000 ký tự. |
| `start_media_id` | Có | Media ID ảnh upload hoặc ảnh vừa sinh. |
| `aspect_ratio` | Không | `VIDEO_ASPECT_RATIO_LANDSCAPE` (mặc định), `VIDEO_ASPECT_RATIO_PORTRAIT`. |
| `quality` | Không | `lite` (mặc định), `fast`, `quality`, `lite_relaxed`, `fast_relaxed`. Một số mức phụ thuộc gói Flow của tài khoản. |

## 8. Tạo Omni video

### Request

```json
{
  "type": "omni",
  "project_id": "978b0a04-9025-431e-aca8-544f37d0757c",
  "prompt": "Create a vertical cinematic product advertisement",
  "reference_media_ids": [
    "b207ef8f-e3ce-44d3-9f2f-043dd0a61275"
  ],
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
  "duration_seconds": 4
}
```

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `type` | Có | Luôn là `omni`. |
| `project_id` | Có | Project chứa các media tham chiếu. |
| `prompt` | Có | 1–12.000 ký tự. |
| `reference_media_ids` | Có | Từ 1 đến 8 media ID. |
| `aspect_ratio` | Không | `VIDEO_ASPECT_RATIO_PORTRAIT` (mặc định), `VIDEO_ASPECT_RATIO_LANDSCAPE`. |
| `duration_seconds` | Không | `2`, `4`, `8` (mặc định), `10`. |

### Response bắt đầu tạo video

Response là body upstream của Flow. Bên gọi cần thu thập tất cả operation name xuất hiện trong response, ví dụ:

```json
{
  "operations": [
    {
      "operation": {
        "name": "operations/VIDEO_OPERATION_ID",
        "done": false
      }
    }
  ]
}
```

Giữ nguyên toàn bộ chuỗi `operation.name`, kể cả prefix nếu Flow trả về.

## 9. Kiểm tra trạng thái video

### Request

```http
POST /v1/videos/status
```

```json
{
  "operation_names": [
    "operations/VIDEO_OPERATION_ID"
  ]
}
```

`operation_names` nhận từ 1 đến 20 phần tử.

### Response đang xử lý

```json
{
  "operations": [
    {
      "operation": {
        "name": "operations/VIDEO_OPERATION_ID",
        "done": false
      }
    }
  ]
}
```

### Response hoàn tất

Cấu trúc media chi tiết do Flow quyết định và có thể bổ sung field theo thời gian. Khi `done` là `true`, đọc các media/video URL trong object kết quả tương ứng:

```json
{
  "operations": [
    {
      "operation": {
        "name": "operations/VIDEO_OPERATION_ID",
        "done": true,
        "response": {
          "media": [
            {
              "name": "VIDEO_MEDIA_ID",
              "video": {
                "generatedVideo": {
                  "fifeUrl": "https://flow-content.google/video/..."
                }
              }
            }
          ]
        }
      }
    }
  ]
}
```

Nếu object operation có `error`, coi tác vụ thất bại và trả lỗi đó về ứng dụng. Không tạo lại mù quáng vì request đầu có thể đã được Flow nhận.

### Khuyến nghị polling

- Poll mỗi 5–10 giây; không gọi liên tục.
- Dừng khi mọi operation có `done: true` hoặc có `error`.
- Đặt timeout nghiệp vụ phù hợp, ví dụ 10 phút.
- HTTP `200` của endpoint status chỉ có nghĩa request kiểm tra hợp lệ; vẫn phải đọc `done`/`error` của từng operation.
- API không trả header `Retry-After`; bên gọi tự quản lý nhịp polling.

## 10. HTTP status và lỗi

Khi Google Flow đã phản hồi, API giữ HTTP status và body upstream, đồng thời thêm header:

```http
X-Flow-Upstream-Status: 200
X-Request-Id: req_...
```

Lỗi do Flow trả về như `403` hoặc `429` vẫn được chuyển tiếp với status tương ứng. Lỗi do lớp API phát sinh dùng envelope chuẩn:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {
        "field": "body.variant_count",
        "code": "less_than_equal",
        "message": "Input should be less than or equal to 4"
      }
    ],
    "request_id": "req_4c0ae693a14c4ec7831b3037803bf132",
    "retryable": false
  }
}
```

| HTTP | Code thường gặp | Ý nghĩa/Xử lý |
|---:|---|---|
| `400` | `INVALID_JSON` | JSON sai cú pháp; sửa request, không retry nguyên trạng. |
| `401` | `INVALID_API_KEY` | Thiếu/sai API key. |
| `403` | Upstream Flow | Tài khoản/quyền/captcha bị từ chối; trả lỗi cho người dùng. |
| `404` | `NOT_FOUND` hoặc upstream | Sai endpoint/resource/media/operation. |
| `409` | `PROJECT_ROUTE_UNKNOWN` | Project tường minh chưa có mapping account; gọi `/v1/projects`, dùng scope v2 đúng account, hoặc dùng managed image flow. |
| `409` | `CONFLICT` | Trạng thái tài nguyên xung đột khác. |
| `413` | `PAYLOAD_TOO_LARGE` | Ảnh Base64 hoặc request quá lớn. |
| `422` | `VALIDATION_ERROR` | Field thiếu hoặc giá trị không thuộc enum/range. |
| `429` | Upstream Flow hoặc rate limit API | Hết quota/bị giới hạn; backoff trước khi thử lại. |
| `502` | `EXTENSION_REQUEST_FAILED` | Extension/Flow trả response không hợp lệ. |
| `503` | `PROVIDER_ACCOUNT_UNAVAILABLE` | Không có extension/tài khoản sẵn sàng; có thể retry với backoff. |
| `503` | `EXTENSION_DISCONNECTED` | Extension mất kết nối; có thể retry. |
| `504` | `EXTENSION_TIMEOUT` | Hết thời gian chờ. Với video, kiểm tra status trước khi tạo lại. |

Luôn ưu tiên xử lý theo `HTTP status`, sau đó theo `error.code`; không phân tích chuỗi `message` để điều khiển nghiệp vụ.

## 11. Hàm gọi API mẫu

```js
const API_URL = "https://api.shopcongngheso5.io.vn";
const API_KEY = process.env.FLOW_PROVIDER_API_KEY;

async function callFlow(path, payload) {
  const requestId = crypto.randomUUID();
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${API_KEY}`,
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
    const error = new Error(`FlowProvider returned ${response.status}`);
    error.status = response.status;
    error.requestId = response.headers.get("x-request-id");
    error.body = body;
    throw error;
  }

  return body;
}
```

## 12. Health check

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

## 13. Checklist cho bên tích hợp

- Lưu API key ở backend/secret manager.
- Tạo hoặc tái sử dụng một `project_id` hợp lý theo nghiệp vụ.
- Giữ mọi media tham chiếu trong cùng project.
- Sau upload, lưu `media.name` làm media ID.
- Sau tạo ảnh, đọc tất cả phần tử `media[]` và lưu URL ngay.
- Sau tạo video, lưu operation name rồi polling có khoảng nghỉ.
- Lưu kết quả ảnh/video về storage của hệ thống tích hợp trước khi URL Flow hết hạn.
- Ghi log `X-Request-Id`, HTTP status và `error.code`; không ghi API key hoặc URL signed đầy đủ vào log công khai.
