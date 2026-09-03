# Hướng Dẫn Toàn Diện Về Video Gemini Omni Flash (`frames_to_video` & `reference_to_video`)

Tài liệu này cung cấp đặc tả kỹ thuật đầy đủ, hướng dẫn tích hợp chi tiết và các ví dụ thực tế cho hệ thống tạo video **Gemini Omni Flash** trên **FlowProviderAPI**.

Hệ thống hoạt động trên nền tảng **Gemini Omni Flash siêu tốc** (thời gian render chỉ từ **10 – 30 giây**, hỗ trợ 4 mức thời lượng: `4s`, `6s`, `8s`, `10s`), được chuẩn hóa theo đúng 2 chế độ cốt lõi của giao diện Google Flow.

---

## 1. Bản Chất 2 Chế Độ Video & Sự Khác Biệt Đầu Vào

| Tiêu chí | Chế độ 1: `frames_to_video` (Khung hình ➔ Video) | Chế độ 2: `reference_to_video` (Ảnh tham chiếu ➔ Video) |
| :--- | :--- | :--- |
| **Thuật ngữ Google Flow UI** | **Frames** (Start Frame / First Frame) | **Ingredients** (Reference Images) |
| **Bản chất hoạt động** | Video **bắt đầu chuyển động chính xác từ giây thứ 0 của bức ảnh đầu vào** (diễn hoạt từ ảnh tĩnh thành chuyển động thực tế). Khung hình đầu không bị AI biến dạng. | AI học hỏi **nhân vật, trang phục, gương mặt, phong cách, chi tiết** từ 1–8 ảnh tham chiếu để sáng tạo một phân cảnh hoàn toàn mới với góc quay tự do. |
| **Tham số chỉ định chế độ (`type`)** | **`"frames_to_video"`** *(khuyên dùng)*<br>*(Alias: `"frames"`, `"start_to_video"`, `"image_to_video"`, `"i2v"`)* | **`"reference_to_video"`** *(khuyên dùng)*<br>*(Alias: `"ingredients"`, `"references"`, `"omni"`, `"r2v"`)* |
| **Tham số ảnh qua Media ID** | `start_media_id`: ID khung hình bắt đầu *(bắt buộc)*<br>`end_media_id`: ID khung hình kết thúc *(tùy chọn nối cảnh)* | `reference_media_ids`: Mảng từ **1 đến 8 ID ảnh** tham chiếu *(bắt buộc)* |
| **Tham số ảnh trực tiếp qua Base64** | `input_images`: Tối đa 2 ảnh Base64 (Ảnh 1 là Start, Ảnh 2 là End) | `input_images`: Mảng từ **1 đến 8 ảnh Base64** tham chiếu |
| **Tính năng chuyển cảnh (Interpolation)** | ✅ **Có**: Biến đổi liền mạch từ ảnh đầu sang ảnh cuối (`end_media_id`) | ❌ **Không**: Ảnh dùng để học phong cách/nhân vật, không cố định khung đầu/cuối |
| **Model nội bộ Google** | `abra_i2v_4s`, `abra_i2v_6s`, `abra_i2v_8s`, `abra_i2v_10s` | `abra_r2v_4s`, `abra_r2v_6s`, `abra_r2v_8s`, `abra_r2v_10s` |
| **Endpoint Google Flow API** | `batchAsyncGenerateVideoStartImage`<br>hoặc `batchAsyncGenerateVideoStartAndEndImage` | `batchAsyncGenerateVideoReferenceImages` |
| **Tỷ lệ khung hình (`aspect_ratio`)** | `"9:16"` (Dọc - Mặc định)<br>`"16:9"` (Ngang) | `"9:16"` (Dọc - Mặc định)<br>`"16:9"` (Ngang) |
| **Thời lượng (`duration_seconds`)** | `4`, `6`, `8` (mặc định), `10` giây | `4`, `6`, `8` (mặc định), `10` giây |

---

## 2. Bảng Chi Phí Credit & Model Mapping

Mỗi request tạo video sẽ tự động trừ trước số credit tương ứng với thời lượng đã chọn để đảm bảo an toàn hạn mức tài khoản:

| Thời lượng (`duration_seconds`) | Model Key `frames_to_video` | Model Key `reference_to_video` | Chi phí Credit | Thời gian render thực tế |
| :---: | :---: | :---: | :---: | :---: |
| **4 giây** | `abra_i2v_4s` | `abra_r2v_4s` | **15 credits** | ~10 - 15 giây |
| **6 giây** | `abra_i2v_6s` | `abra_r2v_6s` | **20 credits** | ~15 - 20 giây |
| **8 giây** (Mặc định) | `abra_i2v_8s` | `abra_r2v_8s` | **25 credits** | ~20 - 30 giây |
| **10 giây** | `abra_i2v_10s` | `abra_r2v_10s` | **30 credits** | ~25 - 35 giây |

---

## 3. Hướng Dẫn Tích Hợp Chi Tiết

### 3.1. Chế độ `frames_to_video` (Khung hình ➔ Video)

> [!IMPORTANT]
> **Khuyến Nghị Chuẩn Toàn Hệ Thống (Multi-Account & Caching):**
> Luôn ưu tiên truyền ảnh trực tiếp qua Base64 (`input_images`) hoặc file local qua MCP (`image_paths`).
> - **Tự động Cache SHA-256 (0ms Deduplication)**: Backend tự động băm content hash và tra cứu trong database. Nếu cùng một ảnh được gửi nhiều lần, backend tái sử dụng ngay `google_media_id` đã có mà hoàn toàn không cần upload lại lên Google.
> - **Tự do phân tải (Load Balancing)**: Khi truyền Base64, backend có thể phân bổ task cho bất kỳ tài khoản Google nào trong cụm còn nhiều credit hoặc rảnh slot nhất. Ngược lại, nếu dùng `start_media_id`, job bị trói cứng vào đúng 1 tài khoản đã tạo ảnh đó (dễ gây lỗi 404 khi chuyển sang tài khoản khác).

#### Cách 1 (Khuyên Dùng Chuẩn): Truyền trực tiếp ảnh Base64 vào `input_images`
```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "frames_to_video",
  "prompt": "Camera zooms in smoothly onto the cyberpunk warrior, glowing neon reflections",
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAAANSUhEUgAA...",
      "mime_type": "image/png"
    }
  ],
  "duration_seconds": 6,
  "aspect_ratio": "9:16"
}
```

#### Cách 2: Nối cảnh từ Khung hình đầu sang Khung hình cuối bằng Base64 (`input_images` 2 phần tử)
```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "frames_to_video",
  "prompt": "Seamless cinematic transition from daytime Tokyo street into nighttime futuristic neon cybercity",
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAAANSUhEUgAA... (Khung bat dau)",
      "mime_type": "image/png"
    },
    {
      "image_base64": "iVBORw0KGgoAAAANSUhEUgAA... (Khung ket thuc)",
      "mime_type": "image/png"
    }
  ],
  "duration_seconds": 8,
  "aspect_ratio": "9:16"
}
```

#### Cách 3 (Tùy chọn đơn tài khoản): Sử dụng `start_media_id` (và tùy chọn `end_media_id`)
*(Lưu ý: Chỉ dùng khi bạn biết chắc chắn video được tạo trên cùng tài khoản Google đã upload media ID này)*
```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "frames_to_video",
  "prompt": "The camera slowly pans around the character, rain drips in slow motion, cinematic lighting",
  "start_media_id": "c61dffd2-2453-4a56-8aef-09c61f096e78",
  "end_media_id": "media-id-khung-cuoi-tuy-chon",
  "duration_seconds": 4,
  "aspect_ratio": "9:16"
}
```

---

### 3.2. Chế độ `reference_to_video` (Ảnh tham chiếu ➔ Video)

> [!IMPORTANT]
> **Khuyến Nghị Chuẩn Toàn Hệ Thống (Multi-Account & Caching):**
> Luôn ưu tiên truyền 1 đến 8 ảnh qua Base64 (`input_images`) hoặc file local qua MCP (`image_paths`).
> Nhờ đó backend tự động deduplicate qua SHA-256 và luân chuyển linh hoạt qua bất kỳ tài khoản Google nào còn credit.

#### Cách 1 (Khuyên Dùng Chuẩn): Truyền trực tiếp danh sách ảnh Base64 vào `input_images` (1 đến 8 ảnh)
```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "reference_to_video",
  "prompt": "The character in the reference images explores an ancient alien temple with torchlight",
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAAANSUhEUgAA... (Anh mat nhan vat)",
      "mime_type": "image/jpeg"
    },
    {
      "image_base64": "iVBORw0KGgoAAAANSUhEUgAA... (Anh trang phuc)",
      "mime_type": "image/jpeg"
    }
  ],
  "duration_seconds": 8,
  "aspect_ratio": "9:16"
}
```

#### Cách 2 (Tùy chọn đơn tài khoản): Sử dụng danh sách `reference_media_ids` (1 đến 8 ảnh)
*(Lưu ý: Chỉ dùng khi các media ID cùng thuộc về một tài khoản Google cụ thể)*
```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "reference_to_video",
  "prompt": "The cyberpunk samurai raises his glowing sword with dynamic action camera, cinematic slow motion",
  "reference_media_ids": [
    "c61dffd2-2453-4a56-8aef-09c61f096e78"
  ],
  "duration_seconds": 4,
  "aspect_ratio": "9:16"
}
```

---

## 4. Cơ Chế Cache Ảnh & Bảo Vệ Database

1. **Không lưu Base64 vào Database SQLite**:
   - Khi nhận `input_images`, chuỗi Base64 chỉ được giữ tạm trên RAM.
   - Trước khi ghi vào SQLite queue, `input_images` bị bóc tách hoàn toàn.
   - Sau khi worker upload xong lên Google Flow, dữ liệu Base64 được giải phóng khỏi RAM ngay lập tức.
2. **Nhận diện ảnh trùng tự động qua SHA-256 (Deduplication)**:
   - Hệ thống tự động băm mã SHA-256 (32 bytes) của nội dung ảnh.
   - Dù bạn có gửi lại cùng 1 file ảnh Base64 nhiều lần, hệ thống nhận diện mã hash và **chỉ upload lên Google Flow đúng 1 lần đầu tiên** (các lần sau tái sử dụng trong 0ms).

---

## 5. Quy Trình Kiểm Tra Trạng Thái & Nhận Kết Quả (`POST /v1/jobs/status`)

### 5.1. Gửi request kiểm tra trạng thái
```http
POST /v1/jobs/status
Content-Type: application/json

{
  "job_ids": [
    "job_c9ece4c5b45048f18fed0a49801d7ba3"
  ]
}
```

### 5.2. Kết quả khi hoàn thành (`status: "complete"`)
```json
{
  "jobs": [
    {
      "id": "job_c9ece4c5b45048f18fed0a49801d7ba3",
      "type": "video",
      "generation_type": "reference_to_video",
      "status": "complete",
      "media": [
        {
          "id": "2c51c2f3-fc4d-4835-86f8-318b2207cda6",
          "type": "video",
          "url": "https://flow-content.google/video/2c51c2f3-fc4d-4835-86f8-318b2207cda6?Expires=1788443969&KeyName=labs-flow-prod-cdn-key&Signature=...",
          "thumbnail_url": "https://flow-content.google/image/2c51c2f3-fc4d-4835-86f8-318b2207cda6?Expires=1788443969&KeyName=labs-flow-prod-cdn-key&Signature=...",
          "width": null,
          "height": null
        }
      ],
      "error": null
    }
  ],
  "metadata": {
    "counts": {
      "queued": 0,
      "running": 0,
      "complete": 1,
      "failed": 0
    },
    "done": true
  }
}
```

### 5.3. Các trường quan trọng trong Response:
- **`job.type`**: Loại media tổng thể (`"video"` hoặc `"image"`).
- **`job.generation_type`**: Định danh chính xác phân loại video (`"frames_to_video"` hoặc `"reference_to_video"`).
- **`media[].url`**: Đường dẫn trực tiếp đến file video MP4 trên Google CDN.
- **`media[].thumbnail_url`**: Đường dẫn ảnh bìa (Poster frame) của video.
- **`metadata.done`**: `true` khi toàn bộ job trong danh sách đã kết thúc (complete hoặc failed), báo hiệu cho client dừng polling.

---

## 6. Xử Lý Lỗi Chuẩn Hóa

Khi một tác vụ render video thất bại (ví dụ: Google Flow chặn prompt nhạy cảm, tài khoản hết credit), response trả về cấu trúc lỗi chuẩn:

```json
{
  "jobs": [
    {
      "id": "job_c9ece4c5b45048f18fed0a49801d7ba3",
      "type": "video",
      "generation_type": "reference_to_video",
      "status": "failed",
      "media": [],
      "error": {
        "code": "PROMPT_BLOCKED_SAFETY",
        "message": "Google Flow blocked this prompt due to safety policy.",
        "retryable": false,
        "outcome_unknown": false
      }
    }
  ]
}
```
