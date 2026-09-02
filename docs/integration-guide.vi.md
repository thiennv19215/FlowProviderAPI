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
- Tạo ảnh trả kết quả đồng bộ sau khi Flow xử lý xong.
- Tạo video trả operation; bên gọi chủ động kiểm tra bằng `/v1/videos/status`.
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
| `POST` | `/v1/videos/status` | Kiểm tra trạng thái các video operation. |
| `GET` | `/health/live` | Kiểm tra process API đang chạy. |
| `GET` | `/health/ready` | Kiểm tra extension/tài khoản Flow sẵn sàng. |

## 3. Contract mapping cho backend

Backend tích hợp chỉ gửi các enum ổn định của FlowProviderAPI. Không gửi trực tiếp
Google model key như `GEM_PIX_2`, `NARWHAL`, `veo_3_1_*` hoặc `abra_r2v_*`;
Provider tự map model nội bộ theo loại request, gói account và thời lượng.

### 3.1. Mapping tạo ảnh

| Giá trị nghiệp vụ của backend | `model` gửi Provider | Ghi chú |
|---|---|---|
| `pro`, `nano-banana-pro` | `NANO_BANANA_PRO` | Mặc định; Provider map sang model ảnh Pro hiện hành. |
| `v2`, `nano-banana-2` | `NANO_BANANA_2` | Provider map sang model ảnh v2 hiện hành. |

| Tỷ lệ UI/backend | `aspect_ratio` gửi Provider |
|---|---|
| `1:1`, `square` | `IMAGE_ASPECT_RATIO_SQUARE` |
| `16:9`, `landscape` | `IMAGE_ASPECT_RATIO_LANDSCAPE` |
| `9:16`, `portrait` | `IMAGE_ASPECT_RATIO_PORTRAIT` |

Nếu bỏ `model`, mặc định là `NANO_BANANA_PRO`. Nếu bỏ `aspect_ratio`, mặc định
là `IMAGE_ASPECT_RATIO_PORTRAIT`.

### 3.2. Mapping image-to-video

Image-to-video không nhận field `model`. Backend gửi `quality`; Provider kết hợp
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
| `16:9`, `landscape` | `VIDEO_ASPECT_RATIO_LANDSCAPE` |
| `9:16`, `portrait` | `VIDEO_ASPECT_RATIO_PORTRAIT` |

Nếu bỏ `quality`, mặc định là `lite`. Nếu bỏ `aspect_ratio`, mặc định là
`VIDEO_ASPECT_RATIO_LANDSCAPE`.

### 3.3. Mapping Omni video

Omni không nhận `model` hoặc `quality`. Backend gửi `duration_seconds`; Provider
tự map sang Omni model tương ứng.

| `duration_seconds` | Model nội bộ Provider tự chọn |
|---:|---|
| `4` | Omni 4 giây |
| `6` | Omni 6 giây |
| `8` | Omni 8 giây, mặc định |
| `10` | Omni 10 giây |

Omni nhận `VIDEO_ASPECT_RATIO_PORTRAIT` hoặc `VIDEO_ASPECT_RATIO_LANDSCAPE`;
mặc định là portrait.

### 3.4. Hàm mapping TypeScript khuyến nghị

```ts
type UiImageModel = "pro" | "v2";
type UiAspect = "1:1" | "16:9" | "9:16";
type UiVideoQuality = "lite" | "fast" | "quality" | "lite_relaxed" | "fast_relaxed";

const IMAGE_MODEL = {
  pro: "NANO_BANANA_PRO",
  v2: "NANO_BANANA_2"
} as const;

const IMAGE_ASPECT = {
  "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
  "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
  "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT"
} as const;

const VIDEO_ASPECT = {
  "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT"
} as const;

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
  3. Kiểm tra tiến độ video qua /v1/videos/status.
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
  "model": "NANO_BANANA_PRO",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
  "variant_count": 1
}
```

Response có `X-Flow-Project-Id`. Nếu chỉ nhận ảnh cuối thì backend không cần điều hướng project; nếu ảnh sẽ tiếp tục làm reference hoặc tạo video, phải lưu cặp `X-Flow-Project-Id + media[i].name` để gửi ở request sau.

Với luồng tách riêng `POST /v1/media` rồi `POST /v1/videos/generations`, caller phải gửi `required_credits` khi upload và chuyển `X-Flow-Project-Id` từ response upload thành `project_id` của request video. Điều này giữ cả hai call trên cùng Chrome extension/account trong triển khai đa account.

Nếu cùng nội dung ảnh đã được upload vào đúng account/project, Provider dùng thẳng media ID trong DB mà không thêm request kiểm tra. Với request managed không truyền `project_id` hoặc routing scope, nếu media ID đã biết thuộc account khác thì Provider tự tải ảnh từ account gốc, upload sang project của account được chọn và thay media ID trước khi chạy job; bytes nguồn chỉ tồn tại trong bộ nhớ trong lúc xử lý. Nếu Google hiếm khi trả `404` vì media cũ, Provider xóa cache, upload lại và retry. Header `X-Flow-Media-Cache-Hits` cho biết số ảnh lấy từ cache trong request.

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

Ảnh vừa sinh cũng có media ID và có thể tiếp tục làm tham chiếu cho lần tạo tiếp theo. Với routing scope, media vẫn phải thuộc đúng project/account. Nếu chỉ truyền `project_id` hoặc bỏ cả hai field route, media đã biết từ account khác có thể được tự rehydrate sang project/account được chọn; gọi lại cùng nội dung không bị báo conflict.

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

## 8. Tạo video từ khung hình (i2v / Image-to-Video)

Sử dụng Gemini Omni Flash (`abra_i2v_*`) để sinh video từ ảnh đầu trong ~12 giây (thay vì Veo 3.1 cũ mất 60-90s). Hỗ trợ cả tùy chọn khung hình cuối (`end_media_id`) để nối cảnh liền mạch.

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
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
  "duration_seconds": 8
}
```

| Field | Bắt buộc | Giá trị hợp lệ |
|---|---:|---|
| `type` | Có | `i2v` (khuyên dùng), `omni_i2v`, hoặc alias cũ `image_to_video`. |
| `project_id` | Không | Bỏ qua để Provider tự dùng managed project mặc định, hoặc truyền nếu muốn chỉ định project. |
| `prompt` | Có | 1–12.000 ký tự mô tả chuyển động. |
| `start_media_id` | Có | ID ảnh làm khung hình xuất phát (hoặc 1 ảnh trong `input_images`). |
| `end_media_id` | Không | ID ảnh làm khung hình kết thúc (tùy chọn: dùng để chuyển cảnh First+Last frame mượt mà). |
| `duration_seconds` | Không | `4`, `6`, `8` (mặc định), `10` giây. |
| `aspect_ratio` | Không | `VIDEO_ASPECT_RATIO_PORTRAIT` (mặc định), `VIDEO_ASPECT_RATIO_LANDSCAPE`. |

## 9. Tạo video từ ảnh tham chiếu (r2v / Reference-to-Video)

Sử dụng Gemini Omni Flash (`abra_r2v_*`) nhận diện nhân vật / phong cách từ 1 đến 8 ảnh tham chiếu để tạo video mới.

### Request

```json
{
  "type": "r2v",
  "prompt": "Create a vertical cinematic product advertisement",
  "reference_media_ids": ["media-id-1", "media-id-2"],
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
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
| `aspect_ratio` | Không | `VIDEO_ASPECT_RATIO_PORTRAIT` (mặc định), `VIDEO_ASPECT_RATIO_LANDSCAPE`. |

### Response bắt đầu tạo video

Response là body upstream của Flow và có hai shape thường gặp. Image-to-video có
thể trả `operations[i].operation.name`; Omni thực tế thường trả
`workflows[i].name` cùng `media[]`. Backend lưu các tên này để gửi vào
`/v1/videos/status`:

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

```json
{
  "workflows": [
    {
      "name": "WORKFLOW_ID",
      "projectId": "978b0a04-9025-431e-aca8-544f37d0757c"
    }
  ],
  "media": [
    {
      "name": "VIDEO_MEDIA_ID",
      "workflowId": "WORKFLOW_ID"
    }
  ]
}
```

Hàm lấy poll name an toàn:

```ts
export function extractVideoPollNames(body: any): string[] {
  const names = [
    ...(body.operations ?? []).map((x: any) => x?.operation?.name ?? x?.name),
    ...(body.workflows ?? []).map((x: any) => x?.name)
  ].filter((x): x is string => typeof x === "string" && x.length > 0);

  // Một số response không có operations/workflows; Provider route media.name.
  if (names.length === 0) {
    names.push(...(body.media ?? [])
      .map((x: any) => x?.name)
      .filter((x: any): x is string => typeof x === "string" && x.length > 0));
  }
  return [...new Set(names)];
}
```

Giữ nguyên toàn bộ chuỗi poll name, kể cả prefix nếu Flow trả về. Không chỉ lấy
`operations[]`, vì làm vậy sẽ bỏ sót response Omni dạng `workflows[]`.

## 10. Kiểm tra trạng thái video

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

Omni/media polling có thể trả shape sau thay cho `operations[]`:

```json
{
  "media": [
    {
      "name": "VIDEO_MEDIA_ID",
      "mediaMetadata": {
        "mediaStatus": {
          "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SCHEDULED"
        }
      },
      "video": {
        "dimensions": { "length": "4s" }
      }
    }
  ]
}
```

### Response hoàn tất

Cấu trúc media chi tiết do Flow quyết định và có thể bổ sung field theo thời gian. Khi video hoàn tất, Provider dùng đúng extension/account đã route để đổi media ID thành signed URL video và thumbnail. Provider chỉ bổ sung hai field ở cấp media là `downloadUrl` và `thumbnailUrl`; các header `X-Flow-Video-Urls` và `X-Flow-Thumbnail-Urls` cho biết số URL đã lấy được. Field gốc mà Flow đã trả sẵn vẫn được giữ nguyên, nhưng Provider không tự tạo alias URL lặp trong `video.generatedVideo`. Signed URL có thời hạn nên backend cần tải/lưu video và thumbnail ngay nếu muốn lưu trữ lâu dài. Nếu Flow chưa cấp URL kịp, poll lại cùng operation thay vì tạo lại video:

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
              "downloadUrl": "https://flow-content.google/video/...",
              "thumbnailUrl": "https://flow-content.google/thumbnail/...",
              "video": {
                "generatedVideo": {}
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

Với response top-level `media[]`, hoàn tất khi
`media[i].mediaMetadata.mediaStatus.mediaGenerationStatus` là
`MEDIA_GENERATION_STATUS_SUCCESSFUL`. Khi đó đọc URL tại hai path chuẩn hóa:

```text
media[i].downloadUrl
media[i].thumbnailUrl
```

### Khuyến nghị polling

- Poll mỗi 5–10 giây; không gọi liên tục.
- Dừng khi mọi operation có `done: true`, có `error`, hoặc mọi media có trạng thái `MEDIA_GENERATION_STATUS_SUCCESSFUL`.
- Đặt timeout nghiệp vụ phù hợp, ví dụ 10 phút.
- HTTP `200` của endpoint status chỉ có nghĩa request kiểm tra hợp lệ; vẫn phải đọc `done`/`error` hoặc `mediaGenerationStatus`.
- API không trả header `Retry-After`; bên gọi tự quản lý nhịp polling.

## 11. HTTP status và lỗi

Backend phải xử lý ba nhóm lỗi độc lập:

1. Provider từ chối request và trả error envelope chuẩn.
2. Google Flow trả HTTP lỗi; Provider chuyển tiếp status/body gần như nguyên bản.
3. Request poll video trả HTTP `200`, nhưng từng operation/media có trạng thái thất bại.

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
- Sau tạo video, dùng `extractVideoPollNames`: lưu `operation.name`, `workflow.name`, hoặc fallback `media.name`, rồi polling có khoảng nghỉ qua `/v1/videos/status`.
- Lưu kết quả ảnh/video về storage của hệ thống tích hợp trước khi URL Flow hết hạn.
- Ghi log `X-Request-Id`, HTTP status và `error.code`; không ghi API key hoặc URL signed đầy đủ vào log công khai.
