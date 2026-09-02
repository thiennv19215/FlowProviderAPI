# Sổ Tay Thực Chiến: Kết Nối AI Agent Với FlowProviderAPI & MCP

Tài liệu này hướng dẫn cách kết nối **bất kỳ AI Agent nào** (Cursor, Claude Desktop, Antigravity, OpenAI/LangChain Agent, script Python/Node.js độc lập) tới hệ thống Google Flow thông qua **FlowProviderAPI** và **MCP (Model Context Protocol)**.

---

## 1. Bản đồ kết nối (Kiến trúc thực tế)

Tùy thuộc vào loại Agent của bạn, chọn 1 trong 2 mô hình sau:

```text
[Mô hình A: Agent hỗ trợ MCP (Cursor, Claude, IDEs, Cline, v.v.)]
AI Agent (MCP Host) ---> [MCP Server: python -m app.mcp_server] ---> [FlowProviderAPI] ---> [Chrome Extension] ---> [Google Flow]

[Mô hình B: Agent tùy biến bằng Code (LangChain, AutoGen, CrewAI, Fastify, v.v.)]
Custom AI Agent Code ---> [FlowProviderAPI + Job Queue] ---> [Background Worker] ---> [Chrome Extension] ---> [Google Flow]
```

### 1.1. Cơ chế Hàng Đợi (Job Queue) & Dispatcher Worker tự động
- **Retry an toàn khi nghẽn:** Video luôn gắn với media/account cụ thể. Khi extension đang bận hoặc chưa đủ credit, API trả lỗi `503` có thể retry thay vì tạo queued-success không bảo đảm route media; hãy retry cùng request sau khi account sẵn sàng.
- **Background Worker (`JobWorker`):** Chạy ngầm liên tục trên VPS, tự động dò tìm tài khoản có đủ credit (`>= 20-25 credits`) và điều phối tạo video ngay khi có slot trống.
- **Đọc trực tiếp từ Database SQLite (1ms):** Khi video render xong, worker tải trước các URL tải video và lưu vào bảng `provider_jobs`. Agent gọi `POST /v1/videos/status` sẽ nhận kết quả tức thì từ database local mà không gây quá tải hoặc chiếm dụng slot của Chrome Extension.

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

### 2.2. Cấu hình cho Codex Desktop/CLI

Project này cung cấp MCP qua `stdio`, không phải Streamable HTTP. Sau khi cài package, chạy:

```powershell
codex mcp add flow-provider `
  --env FLOW_PROVIDER_MCP_BASE_URL=https://api.shopcongngheso5.io.vn `
  --env FLOW_PROVIDER_MCP_ALLOWED_ROOTS=C:\Users\nguye\Documents `
  -- python -m app.mcp_server
```

Kiểm tra bằng `codex mcp list`, restart Codex Desktop, rồi dùng `/mcp` trong Codex CLI/TUI. Trong Codex Desktop có thể cấu hình tương đương tại **Settings → MCP servers → Add server**, chọn **STDIO**, command `python`, args `-m` và `app.mcp_server`.

Không truyền `FLOW_PROVIDER_EXTENSION_API_KEY` cho agent. Secret đó chỉ thuộc kết nối riêng giữa FlowProviderAPI và Chrome extension. MCP adapter hiện không có biến `FLOW_PROVIDER_MCP_API_KEY`.

### 2.3. Cấu hình cho Cursor IDE
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
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Users\\nguye\\Documents\\MyAgentProject"
      }
    }
  }
}
```

---

### 2.4. Cấu hình cho Claude Desktop
Mở file cấu hình Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json` trên Windows hoặc `~/Library/Application Support/Claude/claude_desktop_config.json` trên macOS):

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "flow-provider-mcp",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "https://api.shopcongngheso5.io.vn",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\images;C:\\projects"
      }
    }
  }
}
```

---

### 2.5. Mẫu Prompt thực tế ra lệnh cho Agent

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
headers = {
    "Content-Type": "application/json"
}

def extract_video_poll_names(body):
    """Hỗ trợ response dạng operations, workflows và media của Flow."""
    names = []
    for item in body.get("operations", []):
        operation = item.get("operation") or item
        if isinstance(operation, dict) and operation.get("name"):
            names.append(operation["name"])
    names.extend(
        item["name"] for item in body.get("workflows", [])
        if isinstance(item, dict) and item.get("name")
    )
    if not names:
        names.extend(
            item["name"] for item in body.get("media", [])
            if isinstance(item, dict) and item.get("name")
        )
    return list(dict.fromkeys(names))

def extract_first_media_id(body):
    """Ưu tiên shape media[] hiện tại, giữ fallback images[] để tương thích."""
    for item in body.get("media", []):
        media_id = item.get("name") or item.get("image", {}).get("generatedImage", {}).get("mediaId")
        if media_id:
            return media_id
    for item in body.get("images", []):
        media_id = item.get("media_id") or item.get("name")
        if media_id:
            return media_id
    return None

def find_download_url(value):
    """Tìm URL hoàn tất mà không phụ thuộc vị trí lồng trong upstream response."""
    if isinstance(value, dict):
        for key in ("downloadUrl", "videoUrl", "video_url", "download_url", "fifeUrl", "url"):
            if isinstance(value.get(key), str):
                return value[key]
        for child in value.values():
            url = find_download_url(child)
            if url:
                return url
    elif isinstance(value, list):
        for child in value:
            url = find_download_url(child)
            if url:
                return url
    return None

def find_media_statuses(value):
    statuses = []
    if isinstance(value, dict):
        for key in ("mediaGenerationStatus", "status"):
            status = value.get(key)
            if isinstance(status, str) and status.startswith("MEDIA_GENERATION_STATUS_"):
                statuses.append(status)
        for child in value.values():
            statuses.extend(find_media_statuses(child))
    elif isinstance(value, list):
        for child in value:
            statuses.extend(find_media_statuses(child))
    return statuses

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
    
    media_id = extract_first_media_id(img_data)
    if not media_id:
        raise RuntimeError("Flow không trả về media ID của ảnh")
    image_url = find_download_url(img_data)
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
    poll_names = extract_video_poll_names(vid_data)
    if not poll_names:
        raise RuntimeError("Flow không trả về operation/workflow/media name để polling")
    print(f"Video poll identifiers: {poll_names}")

    # 4. Polling trạng thái Video
    print("\n--- [Bước 3] Đang polling trạng thái video (mỗi 10s) ---")
    download_url = None
    for attempt in range(60):  # Chờ tối đa 10 phút
        time.sleep(10)
        status_resp = requests.post(
            f"{BASE_URL}/v1/videos/status",
            headers=vid_headers,
            json={"operation_names": poll_names}
        )
        status_resp.raise_for_status()
        status_data = status_resp.json()

        operations = [item.get("operation") or item for item in status_data.get("operations", [])]
        operation_error = next((item.get("error") for item in operations if item.get("error")), None)
        if operation_error:
            raise RuntimeError(f"Lỗi tạo video: {operation_error}")

        media = status_data.get("media", [])
        media_error = next((item.get("error") for item in media if item.get("error")), None)
        if media_error:
            raise RuntimeError(f"Lỗi tạo video: {media_error}")

        operations_done = bool(operations) and all(item.get("done") is True for item in operations)
        media_statuses = find_media_statuses(media)
        failed_statuses = {
            "MEDIA_GENERATION_STATUS_UNSUCCESSFUL",
            "MEDIA_GENERATION_STATUS_FAILED",
            "MEDIA_GENERATION_STATUS_CANCELLED",
        }
        if any(status in failed_statuses for status in media_statuses):
            raise RuntimeError(f"Lỗi tạo video: {media_statuses}")
        media_done = bool(media_statuses) and all(
            status == "MEDIA_GENERATION_STATUS_SUCCESSFUL" for status in media_statuses
        )
        download_url = find_download_url(status_data)
        print(f"Poll #{attempt+1}: done={operations_done or media_done}, url={bool(download_url)}")

        if operations_done or media_done:
            if not download_url:
                # Signed URL đôi khi được cấp chậm; tiếp tục poll cùng identifier.
                continue
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
const client = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

function extractFirstMediaId(body: any): string | undefined {
  for (const item of body.media ?? []) {
    const id = item?.name ?? item?.image?.generatedImage?.mediaId;
    if (typeof id === 'string' && id.length > 0) return id;
  }
  for (const item of body.images ?? []) {
    const id = item?.media_id ?? item?.name;
    if (typeof id === 'string' && id.length > 0) return id;
  }
}

function extractVideoPollNames(body: any): string[] {
  const names = [
    ...(body.operations ?? []).map((x: any) => x?.operation?.name ?? x?.name),
    ...(body.workflows ?? []).map((x: any) => x?.name),
  ].filter((x): x is string => typeof x === 'string' && x.length > 0);

  if (names.length === 0) {
    names.push(...(body.media ?? [])
      .map((x: any) => x?.name)
      .filter((x: any): x is string => typeof x === 'string' && x.length > 0));
  }
  return [...new Set(names)];
}

function findDownloadUrl(value: any): string | undefined {
  if (Array.isArray(value)) {
    for (const child of value) {
      const url = findDownloadUrl(child);
      if (url) return url;
    }
  } else if (value && typeof value === 'object') {
    for (const key of ['downloadUrl', 'videoUrl', 'video_url', 'download_url']) {
      if (typeof value[key] === 'string') return value[key];
    }
    for (const child of Object.values(value)) {
      const url = findDownloadUrl(child);
      if (url) return url;
    }
  }
}

function findMediaStatuses(value: any): string[] {
  const statuses: string[] = [];
  if (Array.isArray(value)) {
    for (const child of value) statuses.push(...findMediaStatuses(child));
  } else if (value && typeof value === 'object') {
    for (const key of ['mediaGenerationStatus', 'status']) {
      if (typeof value[key] === 'string' && value[key].startsWith('MEDIA_GENERATION_STATUS_')) {
        statuses.push(value[key]);
      }
    }
    for (const child of Object.values(value)) statuses.push(...findMediaStatuses(child));
  }
  return statuses;
}

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
  const mediaId = extractFirstMediaId(imgRes.data);
  if (!mediaId) throw new Error('Flow không trả về media ID của ảnh');
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

  const pollNames = extractVideoPollNames(vidRes.data);
  if (pollNames.length === 0) {
    throw new Error('Flow không trả về operation/workflow/media name để polling');
  }
  console.log(`Video poll identifiers: ${pollNames.join(', ')}`);

  // 3. Poll
  while (true) {
    await new Promise((r) => setTimeout(r, 10000));
    const statusRes = await client.post(
      '/v1/videos/status',
      { operation_names: pollNames },
      { headers: routingScope ? { 'X-Provider-Routing-Scope': routingScope } : {} },
    );
    const operations = (statusRes.data.operations ?? []).map((x: any) => x?.operation ?? x);
    const operationError = operations.find((x: any) => x?.error)?.error;
    const mediaError = (statusRes.data.media ?? []).find((x: any) => x?.error)?.error;
    if (operationError || mediaError) throw new Error(JSON.stringify(operationError ?? mediaError));

    const operationsDone = operations.length > 0 && operations.every((x: any) => x?.done === true);
    const mediaStatuses = findMediaStatuses(statusRes.data.media ?? []);
    const failedStatuses = new Set([
      'MEDIA_GENERATION_STATUS_UNSUCCESSFUL',
      'MEDIA_GENERATION_STATUS_FAILED',
      'MEDIA_GENERATION_STATUS_CANCELLED',
    ]);
    if (mediaStatuses.some((status) => failedStatuses.has(status))) {
      throw new Error(`Video failed: ${mediaStatuses.join(', ')}`);
    }
    const mediaDone = mediaStatuses.length > 0
      && mediaStatuses.every((status) => status === 'MEDIA_GENERATION_STATUS_SUCCESSFUL');
    const downloadUrl = findDownloadUrl(statusRes.data);
    if ((operationsDone || mediaDone) && downloadUrl) {
      console.log(`Video sẵn sàng: ${downloadUrl}`);
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
