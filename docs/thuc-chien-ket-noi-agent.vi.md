# Sổ Tay Thực Chiến: Kết Nối AI Agent Với FlowProviderAPI & MCP

Tài liệu này hướng dẫn cách kết nối **bất kỳ AI Agent nào** (Cursor, Claude Desktop, Antigravity, OpenAI/LangChain Agent, script Python/Node.js độc lập) tới hệ thống Google Flow thông qua **FlowProviderAPI** và **MCP (Model Context Protocol)**.

---

## 1. Bản đồ kết nối (Kiến trúc thực tế)

Tùy thuộc vào loại Agent của bạn, chọn 1 trong 2 mô hình sau:

```text
[Mô hình A: Agent hỗ trợ MCP (Cursor, Claude, IDEs, Cline, v.v.)]
AI Agent (MCP Host) ---> [MCP Server: python -m app.mcp_server] ---> [FlowProviderAPI] ---> [Chrome Extension] ---> [Google Flow]

[Mô hình B: Agent tùy biến bằng Code (LangChain, AutoGen, CrewAI, Fastify, v.v.)]
Custom AI Agent Code ------------------------(HTTP REST API)-------------------------> [FlowProviderAPI] ---> [Chrome Extension] ---> [Google Flow]
```

---

## 2. Kịch bản 1: Kết nối Agent qua MCP (Claude Desktop, Cursor, Antigravity)

### 2.1. Cài đặt môi trường cho MCP Adapter

Tại máy chạy Agent, bạn chỉ cần Python >= 3.10:

```bash
# Cách A: Cài đặt từ repo local
pip install "C:\path\to\FlowProviderAPI"

# Cách B: Cài đặt trực tiếp qua Git
pip install "git+https://github.com/thiennv19215/FlowProviderAPI.git"
```

---

### 2.2. Cấu hình cho Cursor IDE
Tạo hoặc sửa file `.cursor/mcp.json` trong project của bạn:

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\nguye\\Documents\\FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "https://api.shopcongngheso5.io.vn",
        "FLOW_PROVIDER_MCP_API_KEY": "fpa_prod_YOUR_SECRET_KEY",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Users\\nguye\\Documents\\MyAgentProject"
      }
    }
  }
}
```

---

### 2.3. Cấu hình cho Claude Desktop
Mở file cấu hình Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json` trên Windows hoặc `~/Library/Application Support/Claude/claude_desktop_config.json` trên macOS):

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "flow-provider-mcp",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "https://api.shopcongngheso5.io.vn",
        "FLOW_PROVIDER_MCP_API_KEY": "fpa_prod_YOUR_SECRET_KEY",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\images;C:\\projects"
      }
    }
  }
}
```

---

### 2.4. Mẫu Prompt thực tế ra lệnh cho Agent

Khi MCP đã kết nối, bạn có thể chat tự nhiên với Agent:

> **Prompt tạo ảnh:**
> *"Hãy dùng tool `flow_generate_image` tạo cho tôi 1 bức ảnh concept siêu xe phong cách tương lai Cyberpunk, tỷ lệ 16:9, model pro."*

> **Prompt tạo video từ ảnh:**
> *"Lấy ảnh vừa tạo ở trên, dùng tool `flow_generate_video` với type `image_to_video` để làm chuyển động camera lướt qua xe trong đêm mưa neon. Sau đó poll status cho đến khi có link video tải về."*

---

## 3. Kịch bản 2: AI Agent viết bằng Python (Gọi REST API trực tiếp)

Đây là script hoàn chỉnh từ **Tạo ảnh -> Lấy Media ID -> Tạo Video Veo -> Polling kết quả -> Tải file MP4 về máy**.

```python
import os
import time
import requests

BASE_URL = os.getenv("FLOW_PROVIDER_BASE_URL", "https://api.shopcongngheso5.io.vn")
API_KEY = os.getenv("FLOW_PROVIDER_API_KEY", "fpa_prod_YOUR_SECRET_KEY")

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def generate_full_flow():
    # 1. Kiểm tra trạng thái hệ thống
    health = requests.get(f"{BASE_URL}/health/ready", headers=headers).json()
    print("Health Status:", health)
    if health.get("status") != "ready":
        raise SystemError("FlowProvider hoặc Browser Account chưa sẵn sàng!")

    # 2. Tạo ảnh mẫu
    print("\n--- [Bước 1] Đang tạo ảnh từ prompt ---")
    img_resp = requests.post(
        f"{BASE_URL}/v1/images/generations",
        headers=headers,
        json={
            "prompt": "Cinematic shot of a warrior robot standing on a cliff at sunrise, photorealistic, 8k",
            "model": "NANO_BANANA_PRO",
            "aspect_ratio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            "variant_count": 1
        }
    )
    img_resp.raise_for_status()
    img_data = img_resp.json()
    
    # Lấy routing_scope và project_id từ header/response để đảm bảo nhất quán tài khoản
    routing_scope = img_resp.headers.get("X-Provider-Routing-Scope")
    project_id = img_resp.headers.get("X-Flow-Project-Id")
    
    first_image = img_data["images"][0]
    media_id = first_image.get("media_id") or first_image.get("name")
    image_url = first_image.get("url")
    print(f"Ảnh đã tạo thành công! Media ID: {media_id}")
    print(f"URL ảnh: {image_url}")

    # 3. Tạo Video từ ảnh (Image-to-Video)
    print("\n--- [Bước 2] Đang kích hoạt tạo video Veo ---")
    vid_headers = dict(headers)
    if routing_scope:
        vid_headers["X-Provider-Routing-Scope"] = routing_scope

    vid_resp = requests.post(
        f"{BASE_URL}/v1/videos/generations",
        headers=vid_headers,
        json={
            "type": "image_to_video",
            "project_id": project_id,
            "prompt": "Camera slowly pushes in towards the robot as sunlight flares into the lens",
            "start_media_id": media_id,
            "aspect_ratio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
            "quality": "lite"
        }
    )
    vid_resp.raise_for_status()
    vid_data = vid_resp.json()
    operation_name = vid_data["operations"][0]["name"]
    print(f"Operation Video được khởi tạo: {operation_name}")

    # 4. Polling trạng thái Video
    print("\n--- [Bước 3] Đang polling trạng thái video (mỗi 10s) ---")
    download_url = None
    for attempt in range(60):  # Chờ tối đa 10 phút
        time.sleep(10)
        status_resp = requests.post(
            f"{BASE_URL}/v1/videos/status",
            headers=vid_headers,
            json={"operation_names": [operation_name]}
        )
        status_resp.raise_for_status()
        status_data = status_resp.json()
        op_info = status_data["operations"][0]
        
        print(f"Poll #{attempt+1}: Status={op_info.get('status')}, Done={op_info.get('done')}")
        
        if op_info.get("done"):
            if op_info.get("error"):
                raise RuntimeError(f"Lỗi tạo video: {op_info['error']}")
            download_url = op_info.get("video_url") or op_info.get("download_url")
            break

    if not download_url:
        raise TimeoutError("Hết thời gian chờ video hoàn tất!")

    print(f"\n Video hoàn thành! URL tải về: {download_url}")
    
    # 5. Tải file video về máy
    video_bytes = requests.get(download_url).content
    with open("output_video.mp4", "wb") as f:
        f.write(video_bytes)
    print("Đã lưu video về file: output_video.mp4")

if __name__ == "__main__":
    generate_full_flow()
```

---

## 4. Kịch bản 3: AI Agent viết bằng Node.js / TypeScript

```typescript
import axios from 'axios';

const BASE_URL = process.env.FLOW_PROVIDER_BASE_URL || 'https://api.shopcongngheso5.io.vn';
const API_KEY = process.env.FLOW_PROVIDER_API_KEY || 'fpa_prod_YOUR_SECRET_KEY';

const client = axios.create({
  baseURL: BASE_URL,
  headers: {
    Authorization: `Bearer ${API_KEY}`,
    'Content-Type': 'application/json',
  },
});

async function runFlow() {
  // 1. Sinh ảnh
  const imgRes = await client.post('/v1/images/generations', {
    prompt: 'A cute red panda wearing astronaut suit in space, 3D render',
    model: 'NANO_BANANA_PRO',
    aspect_ratio: 'IMAGE_ASPECT_RATIO_SQUARE',
    variant_count: 1,
  });

  const routingScope = imgRes.headers['x-provider-routing-scope'];
  const projectScope = imgRes.headers['x-flow-project-id'];
  const mediaId = imgRes.data.images[0].media_id;
  console.log(`Media ID: ${mediaId}`);

  // 2. Sinh video
  const vidRes = await client.post(
    '/v1/videos/generations',
    {
      type: 'image_to_video',
      project_id: projectScope,
      prompt: 'Floating in zero gravity with earth in background',
      start_media_id: mediaId,
      aspect_ratio: 'VIDEO_ASPECT_RATIO_PORTRAIT',
      quality: 'lite',
    },
    {
      headers: routingScope ? { 'X-Provider-Routing-Scope': routingScope } : {},
    }
  );

  const operationName = vidRes.data.operations[0].name;
  console.log(`Video Operation: ${operationName}`);

  // 3. Poll
  while (true) {
    await new Promise((r) => setTimeout(r, 10000));
    const statusRes = await client.post('/v1/videos/status', {
      operation_names: [operationName],
    });
    const op = statusRes.data.operations[0];
    if (op.done) {
      console.log(`Video sẵn sàng: ${op.video_url}`);
      break;
    }
    console.log('Đang xử lý video...');
  }
}

runFlow().catch(console.error);
```

---

## 5. Các Quy Tắc Sống Còn Cho AI Agent (Best Practices)

1. **Bảo toàn `X-Provider-Routing-Scope`:**
   - Khi thực hiện chuỗi tác vụ: Upload ảnh -> Tạo ảnh -> Tạo video, hãy luôn truyền header `X-Provider-Routing-Scope` (hoặc tham số `routing_scope` trong MCP) từ response trước sang request sau. Điều này đảm bảo toàn bộ phiên làm việc gắn đúng tài khoản Google Flow sở hữu media đó.

2. **Không Spam Re-create khi Video Đang Pending:**
   - Tạo video tốn credit tài khoản và mất từ 1-3 phút. Nếu status đang pending, agent **phải polling qua tool status**, tuyệt đối không tự ý gọi lại lệnh tạo video mới.

3. **Tải File Ngay Khi Hoàn Thành:**
   - Link Google CDN/Flow media có thời gian hết hạn (expire). Hãy tải ảnh/video về lưu trữ local hoặc S3/R2 ngay khi API trả về kết quả.

4. **Giới Hạn Thư Mục Ảnh (`FLOW_PROVIDER_MCP_ALLOWED_ROOTS`):**
   - Để bảo mật, MCP Server sẽ chặn mọi đường dẫn ảnh nằm ngoài danh sách thư mục được cấu hình trong `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`. Hãy thêm thư mục workspace của bạn vào biến này.
