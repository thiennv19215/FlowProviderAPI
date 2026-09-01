# MCP cho AI agent

FlowProviderAPI có một MCP adapter để các agent gọi Google Flow bằng tool thay vì tự xây HTTP request. Adapter không giữ cookie hoặc token Google; nó gọi business API của FlowProviderAPI, sau đó gateway tiếp tục route qua Chrome extension đang đăng nhập. MCP adapter hiện không gửi API key tới business API.

```text
AI agent -> MCP adapter -> FlowProviderAPI -> Chrome extension -> Google Flow
```

## Cài đặt và chạy

```bash
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Lệnh cài đặt phải nâng package `mcp` lên phiên bản `>=2,<3` theo `pyproject.toml`. Có thể kiểm tra bằng:

```bash
python -c "import importlib.metadata as m; print(m.version('mcp'))"
```

Load `extension/` vào Chrome profile đã đăng nhập Google Flow. MCP adapter mặc định đọc `.env`; nó ưu tiên `FLOW_PROVIDER_MCP_BASE_URL`, sau đó fallback sang `FLOW_PROVIDER_PUBLIC_BASE_URL`, cuối cùng là `http://localhost:8000`.

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

## Kết nối Codex Desktop/CLI

Sau khi đã cài package editable, đăng ký MCP server bằng CLI:

```powershell
codex mcp add flow-provider `
  --env FLOW_PROVIDER_MCP_BASE_URL=https://provider.example.com `
  --env FLOW_PROVIDER_MCP_ALLOWED_ROOTS=C:\absolute\path\to\agent-workspace `
  -- python -m app.mcp_server
```

Kiểm tra bằng `codex mcp list`, sau đó restart Codex Desktop. Trong Codex CLI/TUI có thể dùng `/mcp` để xem server và tool đang hoạt động.

Trong Codex Desktop cũng có thể vào **Settings → MCP servers → Add server**, chọn **STDIO** và nhập:

- Name: `flow-provider`
- Command: `python`
- Args: `-m`, `app.mcp_server`
- Environment: `FLOW_PROVIDER_MCP_BASE_URL` và `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`

Codex Desktop, Codex CLI và IDE extension trên cùng host dùng chung cấu hình MCP. Nếu muốn cấu hình riêng cho repo đã được trust, tạo `.codex/config.toml`:

```toml
[mcp_servers.flow-provider]
command = "python"
args = ["-m", "app.mcp_server"]
cwd = 'C:\absolute\path\to\FlowProviderAPI'
env = { FLOW_PROVIDER_MCP_BASE_URL = "https://provider.example.com", FLOW_PROVIDER_MCP_ALLOWED_ROOTS = 'C:\absolute\path\to\agent-workspace' }
```

Không cấu hình `https://provider.example.com` như một Streamable HTTP MCP URL: URL đó là REST backend mà adapter gọi xuống; MCP server của repo này chỉ hỗ trợ `stdio`.

## Biến môi trường

Khi agent không chạy với `cwd` của repo, cấu hình các biến sau:

```text
FLOW_PROVIDER_MCP_BASE_URL=https://provider.example.com
FLOW_PROVIDER_MCP_TIMEOUT_SECONDS=300
FLOW_PROVIDER_MCP_ALLOWED_ROOTS=C:\absolute\path\to\allowed-images
```

Không có biến `FLOW_PROVIDER_MCP_API_KEY` trong implementation hiện tại. Không đưa `FLOW_PROVIDER_EXTENSION_API_KEY` vào MCP host, prompt, source code hoặc frontend; secret đó chỉ dùng giữa Provider và Chrome extension.

`FLOW_PROVIDER_MCP_ALLOWED_ROOTS` giới hạn các thư mục mà agent được phép đọc ảnh. Mặc định là thư mục chạy MCP (`cwd`). Nếu cần nhiều thư mục, phân tách bằng `;` trên Windows hoặc `:` trên Linux/macOS. Symlink trỏ ra ngoài allowed roots cũng bị từ chối.

## Tools

| Tool | Chức năng |
|---|---|
| `flow_check_health` | Kiểm tra gateway và browser account đã sẵn sàng. |
| `flow_list_projects` | Liệt kê Flow project. |
| `flow_create_project` | Tạo Flow project; thường không cần vì Provider có managed project. |
| `flow_upload_image` | Đọc ảnh local, Base64 và upload để lấy media ID. |
| `flow_generate_image` | Tạo ảnh từ prompt, file tham chiếu hoặc media ID. |
| `flow_generate_video` | Bắt đầu image-to-video hoặc Omni; trả dữ liệu chứa poll identifier. |
| `flow_get_video_status` | Poll từ 1 đến 20 identifier và trả trạng thái/video URL khi hoàn tất. |

Các giá trị MCP được rút gọn cho agent:

- Model ảnh: `pro`, `v2`.
- Tỷ lệ ảnh: `1:1`, `16:9`, `9:16`; mặc định `9:16`.
- Loại video: `image_to_video`, `omni`.
- Tỷ lệ video: `16:9`, `9:16`; mặc định theo loại video.
- Chất lượng image-to-video: `lite`, `fast`, `quality`, `lite_relaxed`, `fast_relaxed`.
- Thời lượng Omni: `4`, `6`, `8`, `10` giây; mặc định `8`.

Adapter tự map các giá trị ngắn sang enum của FlowProviderAPI. Tổng số file ảnh và media ID tham chiếu cho image generation tối đa là 8; Omni cũng nhận tối đa 8 media ID.

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

Khi tạo image-to-video, lấy media ID từ ảnh đã tạo/upload và truyền `metadata.x-flow-project-id` vào `project_id`. Nên tiếp tục truyền `metadata.x-provider-routing-scope` vào `routing_scope` cho workflow gắn với account/project cụ thể. Nếu bỏ `aspect_ratio`, image-to-video mặc định `16:9`, còn Omni mặc định `9:16`.

Khi tạo video thành công, thu thập poll identifier theo thứ tự: `operations[].operation.name` hoặc `operations[].name`, tiếp theo `workflows[].name`; nếu không có các field trên thì dùng `media[].name`. Giữ nguyên prefix và gọi `flow_get_video_status` cho tới khi hoàn tất hoặc có lỗi. Pending là trạng thái bình thường; không tạo operation trả phí mới chỉ vì operation cũ vẫn pending hoặc request trước bị timeout không chắc chắn.

## Kiểm tra bằng MCP Inspector

```bash
mcp dev app/mcp_server.py:mcp
```

Xem thêm: [Sổ tay thực chiến kết nối AI Agent](thuc-chien-ket-noi-agent.vi.md) (Code mẫu Python/Node.js, Cursor, Claude Desktop, xử lý video end-to-end).
