# Tích hợp MCP cho AI Agent

FlowProviderAPI có MCP adapter chạy qua `stdio`. Agent không giữ cookie Google; adapter chỉ gọi REST API, còn FlowProviderAPI mới giao tiếp với Chrome extension đang đăng nhập Google Flow.

```text
AI Agent -> stdio MCP -> FlowProviderAPI REST -> Chrome extension -> Google Flow
```

## 1. Authentication

MCP adapter có hai chế độ:

- Local development: có thể gọi `http://localhost:8000` không cần key nếu API không chạy `FLOW_PROVIDER_ENV=production`.
- Production/remote: phải gửi business Bearer API key giống mọi backend S2S khác.

Cấu hình:

```env
FLOW_PROVIDER_MCP_BASE_URL=https://api.shopcongngheso5.io.vn
FLOW_PROVIDER_MCP_API_KEY=fpa_prod_<business-secret>
FLOW_PROVIDER_MCP_ALLOWED_ROOTS=.
FLOW_PROVIDER_MCP_TIMEOUT_SECONDS=300
```

Nếu `FLOW_PROVIDER_MCP_API_KEY` không được đặt, adapter có thể reuse `FLOW_PROVIDER_BOOTSTRAP_API_KEY` từ cùng environment. Không bao giờ truyền `FLOW_PROVIDER_EXTENSION_API_KEY` cho Agent/MCP.

## 2. Tool chính

| Tool | Mục đích |
|---|---|
| `flow_check_health` | Kiểm tra `/health/ready` trước khi chạy generation. |
| `flow_upload_image` | Đọc file local và upload/cache thành Flow media. |
| `flow_generate_image` | Tạo image job. |
| `flow_generate_video` | Tạo paid video job. |
| `flow_get_job_status` | Đọc trạng thái image/video job từ Provider DB. |
| `flow_create_character` | Tạo Character/location/asset dùng lại. |
| `flow_list_characters` / `flow_get_character` | Đọc catalog Character. |
| `flow_update_character` / `flow_delete_character` | Cập nhật hoặc soft-delete Character. |
| `flow_generate_character_image` | Tạo image job bằng Character reference. |
| `flow_generate_character_video` | Tạo R2V/Omni video bằng Character. |

## 3. Workflow chuẩn

1. Gọi `flow_check_health` nếu chưa biết Provider có account Flow sẵn sàng hay không.
2. Với image/video reference, ưu tiên `image_paths` nằm trong `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`.
3. Gọi tool tạo image/video với `idempotency_key` riêng cho mỗi yêu cầu và lưu `jobs[].id` ngay lập tức. Nếu mất response/timeout, gửi lại đúng key, payload và `routing_scope` để lấy lại job cũ; không đổi key để thử lại paid video khi chưa biết kết quả. Key gồm 1-200 ký tự ASCII in được.
4. Poll `flow_get_job_status` theo nhịp khoảng 10 giây hoặc theo metadata Provider trả về.
5. Không tạo lại paid video chỉ vì job còn `queued`/`running`.
6. Khi `failed`, đọc `error.retryable` và `error.outcome_unknown` trước khi quyết định retry.
7. Khi `complete`, tải output sớm vì URL Google Flow có thể hết hạn.

Public lifecycle:

```text
queued -> running -> complete | failed
```

## 4. Image input và multi-account

MCP tự đọc file local, encode Base64 và gửi vào REST `input_images`. Provider SHA-256 nội dung để deduplicate upload và có thể rehydrate ảnh sang account/project khác khi cần failover.

`flow_generate_image` cho phép tối đa 8 reference tổng cộng. `flow_generate_video` yêu cầu `image_paths` thay vì direct media ID để giữ khả năng multi-account routing:

- frames/image-to-video: 1-2 ảnh.
- reference-to-video/R2V/Omni: 1-8 ảnh.

## 5. Cấu hình Cursor

Ví dụ production:

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

Không commit file cấu hình chứa production key vào Git.

## 6. Cấu hình Claude Desktop

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

## 7. Tool constraints

### Image

- `model`: `pro` hoặc `v2`.
- `aspect_ratio`: `1:1`, `16:9`, `9:16`.
- `variant_count`: 1-4.
- prompt: 1-12000 ký tự.

### Video

- frames aliases: `frames_to_video`, `frames`, `start_to_video`, `image_to_video`, `i2v`.
- reference aliases: `reference_to_video`, `ingredients`, `references`, `omni`, `r2v`.
- `aspect_ratio`: `16:9` hoặc `9:16`.
- `duration_seconds`: 4, 6, 8, 10.
- `flow_get_job_status`: tối đa 20 job IDs/lần.

## 8. Character workflow

1. `flow_upload_image` 1-3 ảnh reference.
2. `flow_create_character` với media IDs vừa nhận.
3. `flow_generate_character_image` hoặc `flow_generate_character_video`.
4. Poll bằng `flow_get_job_status`.

Character giữ snapshot/reference riêng; output mới không tự thay reference catalog.

## 9. Error handling

MCP chuyển lỗi HTTP của Provider thành `ToolError` với HTTP status, error code, message, `retryable` và request ID nếu có. Không retry mù. Với paid request có trạng thái không chắc chắn, kiểm tra job/status trước khi tạo request mới.

REST contract chi tiết nằm tại `docs/integration-guide.vi.md`.
