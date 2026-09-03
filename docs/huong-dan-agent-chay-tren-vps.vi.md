# Hướng Dẫn Tích Hợp AI Agent Chạy Trực Tiếp Trên VPS

Tài liệu này hướng dẫn chi tiết cách triển khai, cấu hình và vận hành **AI Agent (Bot, Automation Workflow, AutoGen, CrewAI, LangChain, Telegram Bot, Cronjob,...)** chạy trực tiếp trên cùng máy chủ VPS với **FlowProviderAPI**.

---

## 1. Kiến Trúc Khi Agent Chạy Trên VPS

Khi Agent chạy cùng VPS, toàn bộ kết nối diễn ra qua mạng nội bộ (`localhost` / `127.0.0.1`), mang lại 3 ưu điểm vượt trội:
- **Độ trễ 0ms (Localhost latency)**: Không bị ảnh hưởng bởi mạng Internet công cộng.
- **Bảo mật tối đa**: Giao tiếp thẳng trong VPS không cần mở port ra ngoài.
- **Tiết kiệm băng thông**: Quá trình chuyển ảnh/video giữa Agent và Provider diễn ra ngay trên ổ cứng và RAM của VPS.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                              UBUNTU VPS                                │
│                                                                        │
│  ┌───────────────────────┐                    ┌─────────────────────┐  │
│  │   AI Agent / Bot      │ ─── stdio / HTTP ─►│ FlowProviderAPI     │  │
│  │ (Python / MCP Client) │ ◄── (127.0.0.1) ───│ (Docker Container)  │  │
│  └───────────────────────┘                    └──────────┬──────────┘  │
│             │                                            │ WebSocket   │
│             ▼ Lưu file MP4/PNG                           ▼             │
│      /home/ubuntu/media/                      Chrome Extension (Headless)
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Kịch Bản 1: Agent Chạy Bằng MCP (Model Context Protocol) Trên VPS

Nếu Agent của bạn sử dụng MCP (ví dụ: Claude Code CLI, Cursor SSH, Codex CLI, Aider, OpenHands):

### 2.1. Cài Đặt Môi Trường MCP Trên VPS
Truy cập VPS qua SSH và cài đặt:
```bash
cd /home/ubuntu/FlowProviderAPI
python3 -m pip install -e ".[dev]"
```

### 2.2. Khởi Chạy MCP Adapter Cho Agent
Cấu hình biến môi trường trỏ thẳng vào container nội bộ:
```bash
export FLOW_PROVIDER_MCP_BASE_URL="http://127.0.0.1:8000"
export FLOW_PROVIDER_MCP_ALLOWED_ROOTS="/home/ubuntu"

# Chạy MCP Server qua stdio:
python3 -m app.mcp_server
```

### 2.3. Cấu hình MCP Client (Codex CLI / Claude Code / Aider) Trên VPS
Thêm vào file cấu hình MCP của Agent:
```json
{
  "mcpServers": {
    "flow-provider": {
      "command": "python3",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/home/ubuntu/FlowProviderAPI",
      "env": {
        "FLOW_PROVIDER_MCP_BASE_URL": "http://127.0.0.1:8000",
        "FLOW_PROVIDER_MCP_ALLOWED_ROOTS": "/home/ubuntu"
      }
    }
  }
}
```

---

## 3. Kịch Bản 2: Agent Viết Bằng Python (Script / Bot / Framework) Trên VPS

Đây là kịch bản phổ biến nhất khi xây dựng Bot tự động, Worker xử lý video hàng loạt hoặc AI Agent tự chủ (Autonomous Agent).

### 3.1. Code Mẫu Hoàn Chỉnh: `agent_vps_runner.py`
Tạo file `/home/ubuntu/agent_vps_runner.py`:

```python
#!/usr/bin/env python3
"""
Agent tự động tạo ảnh và video Omni Flash trực tiếp trên VPS
"""
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
OUTPUT_DIR = Path("/home/ubuntu/media_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def request_api(endpoint: str, payload: dict | None = None) -> dict:
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def check_health() -> bool:
    """1. Kiểm tra trạng thái hệ thống và tài khoản Flow"""
    res = request_api("/health/ready")
    print(f"[*] Health check: {res['status']} | Số tài khoản sẵn sàng: {res.get('provider_accounts', 0)}")
    return res.get("status") == "ready"


def wait_for_job(job_id: str, max_wait_seconds: int = 120, poll_interval: int = 5) -> dict:
    """2. Polling trạng thái job từ Database SQLite nội bộ (0 slot, 0 token)"""
    print(f"[*] Đang theo dõi Job: {job_id}")
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        status_res = request_api("/v1/jobs/status", {"job_ids": [job_id]})
        job = status_res["jobs"][0]
        st = job["status"]
        print(f"    -> Trạng thái: {st}")
        
        if st == "complete":
            return job
        if st == "failed":
            error_msg = job.get("error", {}).get("message", "Unknown error")
            raise RuntimeError(f"Job thất bại: {error_msg}")
        
        time.sleep(poll_interval)
    raise TimeoutError("Quá thời gian chờ render!")


def download_file(url: str, output_path: Path):
    """3. Tải file MP4/PNG về lưu trữ trực tiếp trên VPS"""
    print(f"[*] Đang tải file về: {output_path}")
    urllib.request.urlretrieve(url, output_path)
    print(f"[✓] Đã lưu thành công: {output_path} ({output_path.stat().st_size} bytes)")


# =====================================================================
# WORKFLOW THỰC CHIẾN CỦA AGENT
# =====================================================================
def run_agent_workflow():
    if not check_health():
        print("[!] Hệ thống chưa sẵn sàng, hủy tác vụ.")
        return

    # BƯỚC 1: AGENT TẠO ẢNH CHÂN DUNG NHÂN VẬT (Model v2 / 9:16)
    print("\n=== BƯỚC 1: TẠO ẢNH NHÂN VẬT ===")
    img_payload = {
        "prompt": "A futuristic female cyberpunk warrior with glowing cyan cybernetic eyes, rainy Tokyo neon background, 8k portrait",
        "model": "v2",
        "aspect_ratio": "9:16"
    }
    img_res = request_api("/v1/images/generations", img_payload)
    img_job_id = img_res["jobs"][0]["id"]
    
    # Chờ hoàn thành ảnh (khoảng 10-15s)
    completed_img_job = wait_for_job(img_job_id)
    char_media = completed_img_job["media"][0]
    char_media_id = char_media["id"]
    char_img_url = char_media["url"]
    
    # Lưu ảnh về ổ cứng VPS
    img_file = OUTPUT_DIR / f"{img_job_id}.png"
    download_file(char_img_url, img_file)
    print(f"[✓] Ảnh nhân vật ID: {char_media_id}")

    # BƯỚC 2: AGENT TẠO VIDEO OMNI FLASH TỪ ẢNH NHÂN VẬT (CHUẨN BASE64 MULTI-ACCOUNT)
    print("\n=== BƯỚC 2: TẠO VIDEO OMNI FLASH QUA BASE64 INPUT_IMAGES ===")
    # Đọc file ảnh vừa tải thành chuỗi Base64 để tận dụng SHA-256 deduplication
    # và tự động phân tải đều sang bất kỳ tài khoản Google nào còn credit:
    with open(img_file, "rb") as f:
        char_img_b64 = base64.b64encode(f.read()).decode("ascii")

    vid_payload = {
        "type": "reference_to_video",
        "prompt": "The cyberpunk warrior raises her high-tech plasma katana, slow motion rain dripping from the blade, dynamic camera pan",
        "input_images": [
            {"image_base64": char_img_b64, "mime_type": "image/png"}
        ],
        "duration_seconds": 4,
        "aspect_ratio": "9:16"
    }
    vid_res = request_api("/v1/videos/generations", vid_payload)
    vid_job_id = vid_res["jobs"][0]["id"]
    
    # Chờ render video (khoảng 20-30s)
    completed_vid_job = wait_for_job(vid_job_id, max_wait_seconds=180)
    video_media = completed_vid_job["media"][0]
    video_url = video_media["url"]
    
    # Tải video MP4 về VPS
    vid_file = OUTPUT_DIR / f"{vid_job_id}.mp4"
    download_file(video_url, vid_file)
    
    print("\n=================================================")
    print(f"[✓] HOÀN TẤT QUY TRÌNH!")
    print(f"    - File Ảnh : {img_file}")
    print(f"    - File Video: {vid_file}")
    print("=================================================")


if __name__ == "__main__":
    run_agent_workflow()
```

---

## 4. Chạy Agent Tự Động 24/7 Bằng Systemd Trên VPS

Để Agent của bạn chạy liên tục (ví dụ: lắng nghe webhook, đọc database đơn hàng, sản xuất video tự động):

### 4.1. Tạo Service File Systemd
```bash
sudo bash -c 'cat << "EOF" > /etc/systemd/system/my-ai-agent.service
[Unit]
Description=My Autonomous AI Video Agent
After=network.target docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/usr/bin/python3 /home/ubuntu/agent_vps_runner.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF'
```

### 4.2. Kích hoạt và theo dõi log
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now my-ai-agent
sudo journalctl -u my-ai-agent -f
```

---

## 5. Tối Ưu Hóa & Xử Lý Sự Cố Cho Agent Trên VPS

| Vấn đề | Nguyên nhân | Cách khắc phục |
| :--- | :--- | :--- |
| **`ConnectionRefusedError` (Port 8000)** | Container API đang restart hoặc chưa bật | Chạy `docker compose -f compose.production.yaml ps` để kiểm tra container `flowprovider-production-api-1`. |
| **Hết hạn mức / `provider_accounts: 0`** | Chrome Extension bị ngắt kết nối | Mở Chrome trên VPS / máy tính có cắm Extension, kiểm tra đăng nhập Google Flow. |
| **File video URL hết hạn (Signature expired)** | Signed URL của Google CDN có hạn | Agent cần tải file MP4 về ổ cứng VPS ngay khi `status == "complete"` (như hàm `download_file` ở trên). |
| **Ổ cứng đầy do tải nhiều video** | Video MP4 lưu trữ lâu ngày | Thiết lập cronjob xóa video cũ hơn 7 ngày: `find /home/ubuntu/media_output -type f -mtime +7 -delete`. |
