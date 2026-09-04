# Sổ Tay Thực Chiến: Kết Nối AI Agent Với FlowProviderAPI & MCP

Tài liệu này hướng dẫn cách kết nối **bất kỳ AI Agent nào** (Cursor IDE, Claude Desktop, Windsurf, Python script, Node.js bot, AutoGen, CrewAI, LangChain,...) tới **FlowProviderAPI** chạy trên VPS để tự động tạo ảnh và video Google Flow.

---

## 1. Hai Mô Hình Kết Nối Thực Tế

```text
========================================================================================
MÔ HÌNH A: GỌI TỪ XA (Remote Machine - Laptop / PC / Server khác ➔ VPS)
[Máy tính của bạn (Laptop/PC)] ──────── Internet (HTTP / Base64) ────────► [VPS Provider]
• MCP Adapter chạy trên máy tính để đọc file ảnh local: C:\Users\...
• Endpoint Provider: http://54.255.80.16:8000 (hoặc domain HTTPS)
• Nhận link CDN Google và tải thẳng file MP4 về Laptop của bạn.
========================================================================================
MÔ HÌNH B: GỌI NỘI BỘ (Local on VPS - Agent chạy trực tiếp trên VPS)
[Ubuntu VPS: Agent Script / Worker] ────── Localhost (127.0.0.1:8000) ──────► [Docker API]
• Độ trễ 0ms, không tốn băng thông Internet công cộng.
• Tự động lưu file MP4 vào thư mục /home/ubuntu/media/.
========================================================================================
```

---

## 2. Mô Hình A: Kết Nối Từ Máy Tính Cá Nhân (Remote Client)

### 2.1. Cấu hình MCP cho Cursor IDE trên máy tính (`.cursor/mcp.json`)
Tạo file `.cursor/mcp.json` trong thư mục code trên máy tính của bạn:

```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\nguye\\Documents\\FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "http://54.255.80.16:8000",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Users\\nguye"
      }
    }
  }
}
```

### 2.2. Cấu hình MCP cho Claude Desktop trên máy tính (`claude_desktop_config.json`)
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
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "C:\\Users\\nguye"
      }
    }
  }
}
```

### 2.3. Mẫu Chat/Prompt Thực Tế Ra Lệnh Cho Agent

- **Tạo ảnh từ Prompt**:
  > *"Hãy dùng tool `flow_generate_image` tạo 1 ảnh chân dung nữ chiến binh Cyberpunk, tỷ lệ 9:16, model v2."*

- **Tạo video từ file ảnh trên máy tính của bạn**:
  > *"Hãy dùng tool `flow_generate_video` với type `frames_to_video`, lấy ảnh `C:\Users\nguye\Pictures\character.png` làm khung hình bắt đầu, prompt: 'Camera zoom chậm vào gương mặt, mưa rơi hiệu ứng slow motion', thời lượng 4s. Sau đó theo dõi trạng thái cho đến khi có link video hoàn thành."*

- **Tạo video từ ảnh tham chiếu (Omni Video)**:
  > *"Hãy lấy file ảnh vừa tải về ở trên (truyền qua `image_paths`), gọi `flow_generate_video` với type `reference_to_video`, prompt: 'Nhân vật múa kiếm plasma trong thành phố neon', thời lượng 4s dọc 9:16."*

---

## 3. Code Mẫu Python: Gọi API Từ Xa Hoặc Cục Bộ

Đoạn code Python độc lập dưới đây có thể **chạy trên bất kỳ máy tính nào** (chỉ cần đổi `PROVIDER_URL`):

```python
import time
import json
import base64
import urllib.request
from pathlib import Path

# Cấu hình địa chỉ VPS (hoặc http://127.0.0.1:8000 nếu chạy ngay trên VPS)
PROVIDER_URL = "http://54.255.80.16:8000"


def call_api(endpoint: str, payload: dict | None = None) -> dict:
    url = f"{PROVIDER_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def encode_local_image_base64(file_path: str) -> str:
    """Đọc file ảnh từ ổ cứng máy tính và chuyển sang chuỗi Base64"""
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def wait_for_job_completion(job_id: str, max_wait_seconds: int = 180) -> dict:
    """Polling trạng thái từ Database của Provider (0 slot, 0 token)"""
    print(f"[*] Theo dõi Job ID: {job_id}")
    start = time.time()
    while time.time() - start < max_wait_seconds:
        res = call_api("/v1/jobs/status", {"job_ids": [job_id]})
        job = res["jobs"][0]
        status = job["status"]
        print(f"    -> Trạng thái hiện tại: {status}")
        
        if status == "complete":
            return job
        if status == "failed":
            error_info = job.get("error", {})
            raise RuntimeError(f"Tác vụ thất bại: {error_info.get('message', 'Unknown error')}")
            
        time.sleep(10)
    raise TimeoutError("Quá thời gian chờ hoàn thành job!")


# =====================================================================
# KỊCH BẢN TỰ ĐỘNG HÓA HOÀN CHỈNH
# =====================================================================
def main():
    # 1. Kiểm tra sẵn sàng
    health = call_api("/health/ready")
    print(f"[*] Trạng thái Provider: {health['status']} | Số account sẵn sàng: {health.get('provider_accounts', 0)}")
    if health.get("status") != "ready":
        print("[!] Provider chưa sẵn sàng, vui lòng kiểm tra lại Chrome Extension.")
        return

    # 2. TẠO ẢNH CHÂN DUNG (Model v2 / Tỷ lệ dọc 9:16)
    print("\n--- [Bước 1] Gửi yêu cầu tạo ảnh ---")
    img_payload = {
        "prompt": "A futuristic female cyberpunk samurai with glowing katana in rain, neon city lights, 8k portrait",
        "model": "v2",
        "aspect_ratio": "9:16"
    }
    img_res = call_api("/v1/images/generations", img_payload)
    img_job_id = img_res["jobs"][0]["id"]
    
    # Chờ hoàn thành ảnh (10-15s)
    completed_img = wait_for_job_completion(img_job_id)
    img_media = completed_img["media"][0]
    media_id = img_media["id"]
    img_url = img_media["url"]
    
    # Tải ảnh về máy tính
    urllib.request.urlretrieve(img_url, "avatar_samurai.png")
    print(f"[✓] Đã tạo và tải ảnh về: avatar_samurai.png (Media ID: {media_id})")

    # 3. TẠO VIDEO OMNI FLASH 4S TỪ ẢNH TRÊN (CHUẨN BASE64 MULTI-ACCOUNT)
    # Khuyến nghị: Đọc ảnh vừa tạo thành Base64 và truyền qua input_images.
    # Nhờ đó backend tự động băm SHA-256 và luân chuyển sang bất kỳ tài khoản Google nào
    # còn credit trong cụm mà không bao giờ gặp lỗi 404!
    print("\n--- [Bước 2] Gửi yêu cầu tạo Video Omni Flash qua Base64 input_images ---")
    with open("avatar_samurai.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")

    vid_payload = {
        "type": "reference_to_video",
        "prompt": "The samurai raises her sword as neon rain drips in slow motion, cinematic 4k portrait",
        "input_images": [
            {"image_base64": img_b64, "mime_type": "image/png"}
        ],
        "duration_seconds": 4,
        "aspect_ratio": "9:16"
    }
    vid_res = call_api("/v1/videos/generations", vid_payload)
    vid_job_id = vid_res["jobs"][0]["id"]
    
    # Chờ render video (20-35s)
    completed_vid = wait_for_job_completion(vid_job_id)
    video_media = completed_vid["media"][0]
    video_url = video_media["url"]
    
    # Tải video MP4 trực tiếp từ Google CDN về máy
    urllib.request.urlretrieve(video_url, "samurai_video_4s.mp4")
    print(f"\n[✓] THÀNH CÔNG RỰC RỠ! Đã tải video MP4 về: samurai_video_4s.mp4")


if __name__ == "__main__":
    main()
```

---

## 4. Code Mẫu Node.js / TypeScript: Gọi API Từ Xa

```javascript
import fs from 'fs';
import https from 'https';

const PROVIDER_URL = 'http://54.255.80.16:8000';

async function postJson(endpoint, data) {
  const res = await fetch(`${PROVIDER_URL}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return await res.json();
}

async function waitForJob(jobId) {
  console.log(`[*] Đang theo dõi Job: ${jobId}`);
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 5000));
    const statusRes = await postJson('/v1/jobs/status', { job_ids: [jobId] });
    const job = statusRes.jobs[0];
    console.log(`    -> Trạng thái: ${job.status}`);
    if (job.status === 'complete') return job;
    if (job.status === 'failed') throw new Error(job.error?.message || 'Job failed');
  }
  throw new Error('Timeout!');
}

async function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(destPath);
    https.get(url, (response) => {
      response.pipe(file);
      file.on('finish', () => { file.close(); resolve(); });
    }).on('error', (err) => { fs.unlink(destPath, () => {}); reject(err); });
  });
}

async function run() {
  // 1. Tạo video từ khung hình (frames_to_video) với ảnh Base64
  console.log('[1] Gửi request tạo video Omni Flash 4s...');
  const base64Image = fs.readFileSync('input.png').toString('base64');
  
  const vidRes = await postJson('/v1/videos/generations', {
    type: 'frames_to_video',
    prompt: 'Cinematic slow motion pan across the scene',
    input_images: [{ image_base64: base64Image, mime_type: 'image/png' }],
    duration_seconds: 4,
    aspect_ratio: '9:16'
  });

  const jobId = vidRes.jobs[0].id;
  const completedJob = await waitForJob(jobId);
  const videoUrl = completedJob.media[0].url;

  console.log('[2] Đang tải video MP4 về máy...');
  await downloadFile(videoUrl, 'output_video.mp4');
  console.log('[✓] Đã lưu: output_video.mp4');
}

run().catch(console.error);
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
