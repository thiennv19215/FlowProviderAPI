# Hướng Dẫn Tích Hợp MCP Chuẩn Hóa Cho AI Agent

**FlowProviderAPI** cung cấp một **MCP (Model Context Protocol) Server** tiêu chuẩn qua giao thức `stdio`, giúp mọi AI Agent (Cursor, Claude Desktop, Windsurf, Antigravity, LangChain, AutoGen, CrewAI,...) có thể tự động sinh ảnh và video Google Flow bằng function calling tự nhiên mà không cần tự xử lý HTTP request phức tạp.

```text
┌──────────────┐         ┌─────────────┐         ┌─────────────────┐         ┌──────────────────┐         ┌─────────────┐
│   AI Agent   │ ──stdio─►│ MCP Adapter │ ──HTTP──►│ FlowProviderAPI │ ──WS────►│ Chrome Extension │ ──RPC───►│ Google Flow │
│ (Cursor/...) │ ◄───────│  (Python)   │ ◄───────│    (Gateway)    │ ◄────────│ (Signed-in User) │ ◄────────│  (Web/Labs) │
└──────────────┘         └─────────────┘         └─────────────────┘         └──────────────────┘         └─────────────┘
```

---

## 1. Tool Tối Ưu Hóa Dành Riêng Cho AI Agent

Các tool được tách theo hai nhóm: workflow tổng quát và workflow Character.

| Tên Tool | Loại thao tác | Chức năng & Tối ưu cho Agent |
| :--- | :---: | :--- |
| **`flow_check_health`** | Read-only | Kiểm tra Gateway và trạng thái đăng nhập của tài khoản Google Flow trước khi chạy workflow. |
| **`flow_generate_image`** | Mutating | Tạo ảnh chất lượng cao. Hỗ trợ truyền thẳng file ảnh cục bộ qua `image_paths` hoặc ID ảnh qua `reference_media_ids`. |
| **`flow_generate_video`** | Paid Mutating | Tạo video Gemini Omni Flash siêu tốc (10-25s). Hỗ trợ cả 2 chế độ: **`frames_to_video`** (Start Frame) và **`reference_to_video`** (Ảnh tham chiếu). Cho phép truyền trực tiếp file ảnh local qua `image_paths`. |
| **`flow_upload_image`** | Mutating | Đọc file ảnh local và upload trước để lấy `media_id` dùng cho nhiều video liên tiếp. |
| **`flow_get_job_status`** | Read-only | Đọc trạng thái tác vụ (`queued`, `running`, `complete`, `failed`) trực tiếp từ Database của Provider (0 slot, 0 credit). |
| **`flow_create_character`** | Mutating | Tạo catalog Character/location/asset với tối đa 3 ảnh reference đã upload. |
| **`flow_get_character`** / **`flow_list_characters`** | Read-only | Đọc catalog Character đang hoạt động. |
| **`flow_update_character`** / **`flow_delete_character`** | Mutating | Cập nhật metadata/thay reference hoặc soft-delete Character. |
| **`flow_generate_character_image`** | Mutating | Tạo ảnh mới bằng reference của một Character, có thể thêm ảnh tham chiếu cho riêng lần sinh. |
| **`flow_generate_character_video`** | Paid Mutating | Tạo video R2V/Omni bằng 1 Character và 1-3 reference ảnh. |

---

## 2. Quy Chuẩn Workflow Cho AI Agent (Agent Best Practices)

Khi xây dựng Agent prompt hoặc System Instructions, tuân thủ 5 nguyên tắc sau:

1. **Kiểm tra Readiness**: Gọi `flow_check_health` ở đầu session nếu chưa biết trạng thái hệ thống.
2. **Không cần truyền `project_id`**: Hệ thống đã có **Managed Project tự động** cho từng tài khoản Google. Agent chỉ cần tập trung vào `prompt`, `aspect_ratio`, `duration_seconds` và ảnh đầu vào.
3. **Luôn ưu tiên đọc ảnh qua `image_paths` (MCP) hoặc Base64 (`input_images`) thay vì `media_id`**:
   - **Tự động Cache thông minh**: Backend đã có sẵn cơ chế băm **SHA256 Content Deduplication** trong SQLite. Nếu ảnh trùng, Backend tự động lấy `google_media_id` đã cache sẵn mà **hoàn toàn không cần upload lại lên Google**.
   - **Tự do phân tải (Load Balancing)**: Khi gửi raw bytes / file, Backend có thể linh hoạt chuyển job sang **bất kỳ tài khoản Google nào còn nhiều credit/rảnh slot nhất** trong hệ thống. Ngược lại, nếu dùng `start_media_id` / `reference_media_ids`, job sẽ bị trói cứng vào duy nhất 1 tài khoản đã tạo ra ảnh đó.
   - Với MCP: Agent chỉ cần truyền đường dẫn file: `image_paths: ["./character.png"]`, MCP Adapter sẽ tự động đọc và mã hóa.
4. **Quy trình Polling an toàn**:
   - Sau khi gọi `flow_generate_image`, `flow_generate_video` hoặc một tool Character, Agent nhận về `job_id`.
   - Agent gọi `flow_get_job_status(job_ids=[job_id])` mỗi 5-10 giây cho đến khi `status == "complete"` hoặc `"failed"`.
   - **Tuyệt đối không gửi lặp lại request tạo video** khi job đang ở trạng thái `queued` hoặc `running`.
5. **Tải về URL ngay**: `media[].url` là signed CDN URL có hạn sử dụng, Agent cần tải file về lưu trữ lâu dài.

### Workflow Character

1. Gọi `flow_upload_image` một đến ba lần cho các ảnh của cùng Character.
2. Gọi `flow_create_character` với các `reference_media_ids` trả về.
3. Dùng `flow_generate_character_image` hoặc `flow_generate_character_video`.
   Tool ảnh có thể nhận thêm `reference_media_ids`/`image_paths` cho riêng lần
   sinh; tool video chỉ dùng reference của Character.
4. Poll cùng Provider job ID bằng `flow_get_job_status`. Ảnh Character hoàn tất
   trong một lượt worker; video Character đi qua video poller.

Ảnh output không thay thế ảnh reference. Nếu cần thay toàn bộ reference, dùng
`flow_update_character`; Character đã soft-delete vẫn giữ lịch sử job/status.

---

## 3. Cấu Hình MCP Cho Các Nền Tảng IDE & Agent

### 3.1. Cấu hình Cursor IDE (`.cursor/mcp.json`)
Tạo hoặc sửa file `.cursor/mcp.json` trong thư mục dự án hoặc cấu hình toàn cục:

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\nguye\\Documents\\FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "http://54.255.80.16:8000",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Users\\nguye\\Documents"
      }
    }
  }
}
```

### 3.2. Cấu hình Claude Desktop (`claude_desktop_config.json`)
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\nguye\\Documents\\FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "http://54.255.80.16:8000",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Users\\nguye\\Documents"
      }
    }
  }
}
```

### 3.3. Cấu hình Windsurf IDE (`mcp_config.json`)
```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\nguye\\Documents\\FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "http://54.255.80.16:8000"
      }
    }
  }
}
```

---

## 4. Đặc Tả Tham Số Chi Tiết Của Các Tool

### 4.1. `flow_generate_image` (Tạo ảnh)
- **`prompt`** *(bắt buộc)*: Mô tả chi tiết bức ảnh (1 - 12.000 ký tự).
- **`model`** *(tùy chọn)*: `"pro"` (mặc định - Imagen 3 / GEM_PIX_2) hoặc `"v2"` (Nano Banana 2 / NARWHAL).
- **`aspect_ratio`** *(tùy chọn)*: `"9:16"` (dọc - mặc định), `"1:1"` (vuông), `"16:9"` (ngang).
- **`variant_count`** *(tùy chọn)*: Số lượng biến thể sinh ra từ 1 đến 4 (mặc định: 1).
- **`image_paths`** *(tùy chọn)*: Mảng đường dẫn file ảnh cục bộ trên máy để làm ảnh tham chiếu.
- **`reference_media_ids`** *(tùy chọn)*: Mảng ID ảnh đã có sẵn trên Google Flow.

### 4.2. `flow_generate_video` (Tạo video Omni Flash)
- **`type`** *(bắt buộc)*:
  - **`"frames_to_video"`** (hoặc `"frames"`): Video chuyển động từ **Khung hình bắt đầu**.
  - **`"reference_to_video"`** (hoặc `"ingredients"`): Video dựng từ **Ảnh tham chiếu nhân vật/phong cách**.
- **`prompt`** *(bắt buộc)*: Mô tả hành động, chuyển động camera (1 - 12.000 ký tự).
- **`image_paths`** *(bắt buộc)*: Danh sách đường dẫn file ảnh cục bộ trên máy để làm ảnh đầu vào:
  - Với `frames_to_video`: 1 hoặc 2 file (ảnh xuất phát và tùy chọn ảnh kết thúc nối cảnh).
  - Với `reference_to_video`: từ 1 đến 8 file ảnh tham chiếu.
  - *Lưu ý*: Không truyền trực tiếp `media_id` nữa. Việc bắt buộc truyền file ảnh cục bộ giúp hệ thống tự động băm SHA-256, tự động upload khi cần, và **tự do phân tải (load balancing) sang bất kỳ tài khoản Google Flow nào còn đủ credits**, loại bỏ triệt để hiện tượng dồn tải và nghẽn queue.
- **`duration_seconds`** *(tùy chọn)*: `4`, `6`, `8` (mặc định), `10` giây (tương ứng 15, 20, 25, 30 credits).
- **`aspect_ratio`** *(tùy chọn)*: `"9:16"` (dọc - mặc định) hoặc `"16:9"` (ngang).

### 4.3. `flow_get_job_status` (Kiểm tra trạng thái tác vụ)
- **`job_ids`** *(bắt buộc)*: Mảng từ 1 đến 20 mã `job_id` nhận được từ lệnh tạo.

### 4.4. `flow_create_character` và `flow_update_character`

- `name`, `entity_type`, `description`, `voice_description`: metadata tùy chọn
  theo catalog.
- `reference_media_ids`: tối đa 3 ảnh đã upload qua `flow_upload_image`.
- `flow_update_character` thay toàn bộ danh sách reference khi field này được
  truyền; truyền `[]` để xóa reference.

### 4.5. `flow_generate_character_image`

- `character_id`, `prompt` là bắt buộc.
- `model`: `pro` hoặc `v2`; `aspect_ratio`: `1:1`, `16:9`, `9:16`;
  `variant_count`: 1-4.
- Có thể truyền thêm `reference_media_ids` hoặc `image_paths` cho riêng lần
  sinh này. Provider luôn tự kèm reference của Character; tổng số ảnh duy nhất
  (Character + ảnh thêm) tối đa 8. Ảnh thêm không làm thay đổi catalog Character.

### 4.6. `flow_generate_character_video`

- `character_id`, `prompt` là bắt buộc; một request chỉ dùng một Character.
- `aspect_ratio`: `16:9` hoặc `9:16`; `duration_seconds`: 4, 6, 8 hoặc 10.
- `dialogue` mặc định `false`; khi bật, Provider nối `voice_description` vào
  prompt snapshot. Tool không có `start_media_id` vì đây là R2V, không phải I2V.

---

## 5. Ví Dụ Function Calling Thực Tế

### Ví dụ 1: Agent tạo ảnh từ mô tả
```json
{
  "name": "flow_generate_image",
  "arguments": {
    "prompt": "A futuristic cyborg cat, neon cyberpunk lighting, 4k highly detailed",
    "model": "v2",
    "aspect_ratio": "9:16"
  }
}
```

### Ví dụ 2: Agent tạo video 4s từ ảnh local trên máy tính
```json
{
  "name": "flow_generate_video",
  "arguments": {
    "type": "frames_to_video",
    "prompt": "Camera zooms in as rain falls in slow motion",
    "image_paths": ["C:\\Users\\nguye\\Pictures\\samurai.png"],
    "duration_seconds": 4,
    "aspect_ratio": "9:16"
  }
}
```

### Ví dụ 3: Agent kiểm tra kết quả và lấy video URL
```json
{
  "name": "flow_get_job_status",
  "arguments": {
    "job_ids": ["job_c9ece4c5b45048f18fed0a49801d7ba3"]
  }
}
```

---

## 6. Kiểm Tra MCP Bằng MCP Inspector

Bạn có thể mở giao diện đồ họa test tương tác của MCP bất kỳ lúc nào bằng lệnh:

```bash
mcp dev app/mcp_server.py:mcp
```
