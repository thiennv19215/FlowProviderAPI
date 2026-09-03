# Hướng Dẫn Tích Hợp MCP Chuẩn Hóa Cho AI Agent

**FlowProviderAPI** cung cấp một **MCP (Model Context Protocol) Server** tiêu chuẩn qua giao thức `stdio`, giúp mọi AI Agent (Cursor, Claude Desktop, Windsurf, Antigravity, LangChain, AutoGen, CrewAI,...) có thể tự động sinh ảnh và video Google Flow bằng function calling tự nhiên mà không cần tự xử lý HTTP request phức tạp.

```text
┌──────────────┐         ┌─────────────┐         ┌─────────────────┐         ┌──────────────────┐         ┌─────────────┐
│   AI Agent   │ ──stdio─►│ MCP Adapter │ ──HTTP──►│ FlowProviderAPI │ ──WS────►│ Chrome Extension │ ──RPC───►│ Google Flow │
│ (Cursor/...) │ ◄───────│  (Python)   │ ◄───────│    (Gateway)    │ ◄────────│ (Signed-in User) │ ◄────────│  (Web/Labs) │
└──────────────┘         └─────────────┘         └─────────────────┘         └──────────────────┘         └─────────────┘
```

---

## 1. 5 Tool Tối Ưu Hóa Dành Riêng Cho AI Agent

Toàn bộ nghiệp vụ đã được cô đọng thành đúng 5 tool mạnh mẽ, không có dư thừa:

| Tên Tool | Loại thao tác | Chức năng & Tối ưu cho Agent |
| :--- | :---: | :--- |
| **`flow_check_health`** | Read-only | Kiểm tra Gateway và trạng thái đăng nhập của tài khoản Google Flow trước khi chạy workflow. |
| **`flow_generate_image`** | Mutating | Tạo ảnh chất lượng cao. Hỗ trợ truyền thẳng file ảnh cục bộ qua `image_paths` hoặc ID ảnh qua `reference_media_ids`. |
| **`flow_generate_video`** | Paid Mutating | Tạo video Gemini Omni Flash siêu tốc (10-25s). Hỗ trợ cả 2 chế độ: **`frames_to_video`** (Start Frame) và **`reference_to_video`** (Ảnh tham chiếu). Cho phép truyền trực tiếp file ảnh local qua `image_paths`. |
| **`flow_upload_image`** | Mutating | Đọc file ảnh local và upload trước để lấy `media_id` dùng cho nhiều video liên tiếp. |
| **`flow_get_job_status`** | Read-only | Đọc trạng thái tác vụ (`queued`, `running`, `complete`, `failed`) trực tiếp từ Database của Provider (0 slot, 0 credit). |

---

## 2. Quy Chuẩn Workflow Cho AI Agent (Agent Best Practices)

Khi xây dựng Agent prompt hoặc System Instructions, tuân thủ 5 nguyên tắc sau:

1. **Kiểm tra Readiness**: Gọi `flow_check_health` ở đầu session nếu chưa biết trạng thái hệ thống.
2. **Không cần truyền `project_id`**: Hệ thống đã có **Managed Project tự động** cho từng tài khoản Google. Agent chỉ cần tập trung vào `prompt`, `aspect_ratio`, `duration_seconds` và ảnh đầu vào.
3. **Đọc ảnh local cực dễ qua `image_paths`**:
   - Thay vì phải tự viết code convert Base64, Agent chỉ cần truyền đường dẫn file:
     `image_paths: ["./character.png"]`
   - MCP Server sẽ tự động kiểm tra bảo mật trong `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`, đọc file và mã hóa tự động.
4. **Quy trình Polling an toàn**:
   - Sau khi gọi `flow_generate_image` hoặc `flow_generate_video`, Agent nhận về `job_id`.
   - Agent gọi `flow_get_job_status(job_ids=[job_id])` mỗi 5-10 giây cho đến khi `status == "complete"` hoặc `"failed"`.
   - **Tuyệt đối không gửi lặp lại request tạo video** khi job đang ở trạng thái `queued` hoặc `running`.
5. **Tải về URL ngay**: `media[].url` là signed CDN URL có hạn sử dụng, Agent cần tải file về lưu trữ lâu dài.

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
- **`duration_seconds`** *(tùy chọn)*: `4`, `6`, `8` (mặc định), `10` giây (tương ứng 15, 20, 25, 30 credits).
- **`aspect_ratio`** *(tùy chọn)*: `"9:16"` (dọc - mặc định) hoặc `"16:9"` (ngang).
- **Đầu vào cho `frames_to_video`**:
  - `start_media_id`: ID khung hình xuất phát.
  - `end_media_id` *(tùy chọn)*: ID khung hình kết thúc (dùng khi nối cảnh First+Last frame).
  - Hoặc `image_paths`: Danh sách 1–2 file ảnh cục bộ.
- **Đầu vào cho `reference_to_video`**:
  - `reference_media_ids`: Danh sách từ 1 đến 8 ID ảnh tham chiếu.
  - Hoặc `image_paths`: Danh sách từ 1 đến 8 file ảnh cục bộ.

### 4.3. `flow_get_job_status` (Kiểm tra trạng thái tác vụ)
- **`job_ids`** *(bắt buộc)*: Mảng từ 1 đến 20 mã `job_id` nhận được từ lệnh tạo.

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
