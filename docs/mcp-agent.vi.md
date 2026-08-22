# MCP cho AI agent

FlowProviderAPI có một MCP adapter để các agent gọi Google Flow bằng tool thay vì tự xây HTTP request. Adapter không giữ cookie hoặc token Google; nó gọi FlowProviderAPI bằng API key, sau đó gateway tiếp tục route qua Chrome extension đang đăng nhập.

```text
AI agent -> MCP adapter -> FlowProviderAPI -> Chrome extension -> Google Flow
```

## Cài đặt và chạy

```bash
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Load `extension/` vào Chrome profile đã đăng nhập Google Flow. MCP adapter mặc định đọc `.env`, dùng `FLOW_PROVIDER_PUBLIC_BASE_URL` và `FLOW_PROVIDER_BOOTSTRAP_API_KEY` hiện có.

Agent local nên dùng transport `stdio`:

```bash
python -m app.mcp_server
```

Hoặc dùng executable được cài từ package:

```bash
flow-provider-mcp
```

Ví dụ cấu hình chung cho MCP host, thay `cwd` bằng đường dẫn tuyệt đối của repo:

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\absolute\\path\\to\\FlowProviderAPI"
    }
  }
}
```

Nếu agent không chạy với `cwd` của repo, truyền các biến môi trường sau trong cấu hình MCP host:

```text
FLOW_PROVIDER_MCP_BASE_URL=https://provider.example.com
FLOW_PROVIDER_MCP_API_KEY=fpa_prod_<secret>
FLOW_PROVIDER_MCP_TIMEOUT_SECONDS=300
FLOW_PROVIDER_MCP_ALLOWED_ROOTS=C:\absolute\path\to\allowed-images
```

`FLOW_PROVIDER_MCP_ALLOWED_ROOTS` giới hạn các thư mục mà agent được phép đọc ảnh. Mặc định là thư mục chạy MCP (`cwd`). Nếu cần nhiều thư mục, phân tách bằng `;` trên Windows hoặc `:` trên Linux/macOS. Symlink trỏ ra ngoài allowed roots cũng bị từ chối.

Không đưa MCP API key vào prompt, source code hoặc frontend. Adapter chỉ hỗ trợ `stdio`; không mở HTTP MCP endpoint khi chưa có cơ chế xác thực caller.

## Tools

| Tool | Chức năng |
|---|---|
| `flow_check_health` | Kiểm tra gateway và browser account đã sẵn sàng. |
| `flow_list_projects` | Liệt kê Flow project. |
| `flow_create_project` | Tạo Flow project; thường không cần vì Provider có managed project. |
| `flow_upload_image` | Đọc ảnh local, Base64 và upload để lấy media ID. |
| `flow_generate_image` | Tạo ảnh từ prompt, file tham chiếu hoặc media ID. |
| `flow_generate_video` | Bắt đầu image-to-video hoặc Omni; trả operation name. |
| `flow_get_video_status` | Poll operation video và trả URL khi hoàn tất. |

Các giá trị MCP được rút gọn cho agent: model ảnh là `pro`/`v2`, tỷ lệ là `1:1`/`16:9`/`9:16`. Adapter tự map sang enum của FlowProviderAPI.

Mọi kết quả thành công có dạng:

```json
{
  "status_code": 200,
  "data": {},
  "metadata": {
    "x-request-id": "req_...",
    "x-flow-project-id": "projects/...",
    "x-provider-routing-scope": "..."
  }
}
```

Khi tạo image-to-video, lấy media ID từ ảnh đã tạo/upload và truyền `metadata.x-flow-project-id` vào `project_id`. Nếu bỏ `aspect_ratio`, image-to-video mặc định `16:9`, còn Omni mặc định `9:16`. Khi tạo video thành công, lưu operation name rồi gọi `flow_get_video_status` cho tới khi `done=true`. Không tạo operation trả phí mới chỉ vì operation cũ vẫn pending.

## Kiểm tra bằng MCP Inspector

```bash
mcp dev app/mcp_server.py:mcp
```
