# Hướng Dẫn Toàn Diện Về Gemini Omni Flash Video (`i2v` & `r2v`)

Tài liệu này cung cấp đặc tả kỹ thuật, hướng dẫn tích hợp và thực hành tốt nhất cho hệ thống sinh video **Gemini Omni Flash** trên **FlowProviderAPI**.

Hệ thống đã loại bỏ hoàn toàn họ model Veo 3.1 cũ (thời gian render 60–90 giây, chi phí lớn) và chuyển dịch 100% sang kiến trúc **Gemini Omni Flash** siêu tốc (thời gian render chỉ **10–15 giây**, hỗ trợ các mức thời lượng từ 4s đến 10s).

---

## 1. So Sánh Hai Chế Độ: `i2v` vs `r2v`

Hệ thống phân tách thành hai nhánh độc lập dựa trên bản chất đồ họa đầu vào:

| Tiêu chí | Chế độ `i2v` (Khung hình ➔ Video) | Chế độ `r2v` (Ảnh tham chiếu ➔ Video) |
| :--- | :--- | :--- |
| **Bản chất AI** | Bắt buộc video phải bắt đầu chính xác từ 1 khung hình xuất phát (Frame 0 không bị biến dạng). | AI học đặc điểm nhân vật, trang phục, sản phẩm từ ảnh tham chiếu để tự do sáng tạo phân cảnh và góc máy mới. |
| **Trường ảnh đầu vào** | `start_media_id` (khung đầu)<br>`end_media_id` (khung cuối - tùy chọn nối cảnh) | `reference_media_ids` (danh sách 1 đến 8 ID ảnh tham chiếu) |
| **Endpoint Google Flow** | `batchAsyncGenerateVideoStartImage`<br>hoặc `batchAsyncGenerateVideoStartAndEndImage` (khi có `end_media_id`) | `batchAsyncGenerateVideoReferenceImages` |
| **Dòng Model ngầm** | `abra_i2v_4s`, `abra_i2v_6s`, `abra_i2v_8s`, `abra_i2v_10s` | `abra_r2v_4s`, `abra_r2v_6s`, `abra_r2v_8s`, `abra_r2v_10s` |
| **Thời lượng hỗ trợ** | `4`, `6`, `8` (mặc định), `10` giây | `4`, `6`, `8` (mặc định), `10` giây |
| **Chi phí Credit** | 4s: 15 \| 6s: 20 \| 8s: 25 \| 10s: 30 credits | 4s: 15 \| 6s: 20 \| 8s: 25 \| 10s: 30 credits |
| **Tỷ lệ khung hình** | `VIDEO_ASPECT_RATIO_PORTRAIT` (9:16 - mặc định)<br>`VIDEO_ASPECT_RATIO_LANDSCAPE` (16:9) | `VIDEO_ASPECT_RATIO_PORTRAIT` (9:16 - mặc định)<br>`VIDEO_ASPECT_RATIO_LANDSCAPE` (16:9) |
| **Tính năng nối cảnh** | ✅ Hỗ trợ First + Last Frame chuyển cảnh mượt mà | ❌ Không áp dụng |

---

## 2. Bảng Chi Phí Credit & Model Wire Keys

Mỗi request sinh video sẽ tự động trừ trước số credit tương ứng với thời lượng đã chọn để đảm bảo an toàn hạn mức:

| Thời lượng (`duration_seconds`) | Model Wire Key `i2v` | Model Wire Key `r2v` | Chi phí Credit | Thời gian render thực tế |
| :---: | :---: | :---: | :---: | :---: |
| **4 giây** | `abra_i2v_4s` | `abra_r2v_4s` | **15 credits** | ~10 - 12 giây |
| **6 giây** | `abra_i2v_6s` | `abra_r2v_6s` | **20 credits** | ~12 - 15 giây |
| **8 giây** (Mặc định) | `abra_i2v_8s` | `abra_r2v_8s` | **25 credits** | ~15 - 18 giây |
| **10 giây** | `abra_i2v_10s` | `abra_r2v_10s` | **30 credits** | ~18 - 22 giây |

---

## 3. Hướng Dẫn Tích Hợp Chi Tiết

### 3.1. Chế độ `i2v` (Tạo video từ khung hình)

#### Trường hợp 1: Có 1 khung hình đầu (`start_media_id`)
Video bắt đầu chính xác từ ảnh này và diễn hoạt chuyển động theo mô tả prompt.

```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "i2v",
  "prompt": "The camera slowly pans around the girl as neon rain reflections shimmer in the night, cinematic slow motion",
  "start_media_id": "1f68b8ec-7e46-41b3-81f6-38a7a1d0a769",
  "duration_seconds": 6,
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"
}
```

#### Trường hợp 2: Có khung hình đầu VÀ khung hình cuối (`start_media_id` + `end_media_id`)
AI sẽ tự động nội suy chuyển cảnh liền mạch từ ảnh đầu biến chuyển thành đúng ảnh cuối. Rất hữu ích khi nối Cảnh N sang Cảnh N+1:

```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "i2v",
  "prompt": "Smooth morphing transition from daylight city skyline into nighttime neon street",
  "start_media_id": "media-khung-dau",
  "end_media_id": "media-khung-cuoi",
  "duration_seconds": 8,
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"
}
```

---

### 3.2. Chế độ `r2v` (Tạo video từ ảnh tham chiếu)

Dùng khi bạn có ảnh chân dung nhân vật hoặc hình ảnh sản phẩm và muốn AI tạo video ở bối cảnh mới hoàn toàn mà vẫn giữ nguyên gương mặt / trang phục:

```http
POST /v1/videos/generations
Content-Type: application/json

{
  "type": "r2v",
  "prompt": "The cyber hacker character walks down a bustling futuristic Tokyo street smiling at the neon signs, dynamic cinematic camera",
  "reference_media_ids": [
    "1f68b8ec-7e46-41b3-81f6-38a7a1d0a769"
  ],
  "duration_seconds": 4,
  "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"
}
```

---

## 4. Quy Trình Polling Trạng Thái Video

Sau khi gọi `POST /v1/videos/generations`, server sẽ trả về mã `workflows` hoặc `operations`. Bạn dùng mã này để polling trạng thái:

### Request Polling
```http
POST /v1/videos/status
Content-Type: application/json

{
  "operation_names": [
    "637e4a84-96a7-4319-af90-835674d66c4a"
  ]
}
```

### Response Khi Đang Render
```json
{
  "remainingCredits": 201,
  "media": [
    {
      "name": "5b50a8e4-7b71-44ae-b2d4-019e7794ef33",
      "mediaMetadata": {
        "mediaStatus": {
          "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SCHEDULED"
        }
      }
    }
  ]
}
```

### Response Khi Render Thành Công (Kèm Link Tải)
Khi `mediaGenerationStatus` chuyển sang `MEDIA_GENERATION_STATUS_SUCCESSFUL`, response sẽ có trường `downloadUrl` và `thumbnailUrl`:

```json
{
  "remainingCredits": 201,
  "media": [
    {
      "name": "5b50a8e4-7b71-44ae-b2d4-019e7794ef33",
      "mediaMetadata": {
        "mediaTitle": "The cyber hacker character walks down a bustling futuristic Tokyo street",
        "mediaStatus": {
          "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        },
        "mediaBlobSize": "2845434"
      },
      "video": {
        "generatedVideo": {
          "model": "abra_r2v_4s",
          "aspectRatio": "VIDEO_ASPECT_RATIO_PORTRAIT"
        },
        "dimensions": {
          "length": "4s"
        }
      },
      "downloadUrl": "https://flow-content.google/video/5b50a8e4-7b71-44ae-b2d4-019e7794ef33?Expires=1788394001&KeyName=labs-flow-prod-cdn-key&Signature=MW2MCUIrhThGVc5FrEspX3qxrTA",
      "thumbnailUrl": "https://flow-content.google/image/5b50a8e4-7b71-44ae-b2d4-019e7794ef33?Expires=1788394001&KeyName=labs-flow-prod-cdn-key&Signature=Bv2cztCTsUSQJ9zb1w342JumiTo"
    }
  ]
}
```

> **Lưu ý quan trọng:** `downloadUrl` là Google Cloud Storage Signed URL có thời hạn hết hạn (`Expires=...`). Hãy tải file video về lưu trữ cục bộ hoặc S3 ngay sau khi hoàn tất.

---

## 5. Tích Hợp Dành Cho AI Agent Qua MCP (Model Context Protocol)

AI Agent kết nối qua MCP Server có thể gọi trực tiếp tool `flow_generate_video`:

### Gọi `i2v` qua MCP:
```json
{
  "name": "flow_generate_video",
  "arguments": {
    "type": "i2v",
    "prompt": "Camera pushes forward into the room, cinematic lighting",
    "start_media_id": "1f68b8ec-7e46-41b3-81f6-38a7a1d0a769",
    "end_media_id": "media-id-optional-for-transition",
    "duration_seconds": 6,
    "aspect_ratio": "9:16"
  }
}
```

### Gọi `r2v` qua MCP:
```json
{
  "name": "flow_generate_video",
  "arguments": {
    "type": "r2v",
    "prompt": "The subject gives an energetic presentation on stage",
    "reference_media_ids": ["1f68b8ec-7e46-41b3-81f6-38a7a1d0a769"],
    "duration_seconds": 4,
    "aspect_ratio": "9:16"
  }
}
```

---

## 6. Tính Tương Thích Ngược (Backward Compatibility)

Hệ thống thiết kế tương thích 100% với các client hoặc script cũ:
- Gửi `type: "image_to_video"` hoặc `type: "omni_i2v"` ➔ Tự động xử lý theo nhánh **`i2v`**.
- Gửi `type: "omni"` hoặc `type: "omni_r2v"` ➔ Tự động xử lý theo nhánh **`r2v`**.
- Nếu không truyền `duration_seconds`, hệ thống dùng mặc định là **8 giây** (`abra_*_8s`).
