# Quan sát RPC trên Flow thật

Ngày ghi nhận: 2026-09-07. Nguồn: Chrome đang đăng nhập, trang chủ và một project có sẵn trên `https://flow.google.com`.

## Phương pháp và giới hạn

Thao tác trực tiếp bằng browser UI: mở thông tin ảnh có sẵn, tải trang chủ, tải project, chọn bộ lọc Hình ảnh. Đọc inventory tài nguyên đã quan sát của trang; chỉ giữ origin/path và tham số `rpcids`. Không lưu cookie, bearer, query đầy đủ, URL media có chữ ký hoặc nội dung project.

Đây là quan sát URL tài nguyên/request, **không phải HAR hoặc network trace có body**. Công cụ hiện tại không cung cấp HTTP method, status, request payload, response body hoặc initiator stack. Chưa thực hiện upload, tạo project, generation hoặc thực thi CAPTCHA. Không suy diễn API cũ đã ngừng hoạt động từ việc không thấy chúng trong các thao tác này.

## Endpoint quan sát được

`https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute`

Frontend được tải: `boq-labs-ai-sandbox.AiSandboxAngularFrontend`.

| Ngữ cảnh tải trang | rpcids quan sát được |
| --- | --- |
| Trang chủ | `cPZSdc`, `nzlxg`, `o30O0e`, `NfrxTb`, `Yizz8d`, `KV2T2d`, `xI9TVb`, `UpteDb` |
| Project | `cPZSdc`, `nzlxg`, `o30O0e`, `NfrxTb`, `Yizz8d`, `KV2T2d`, `HTrJv`, `LPzVkd`, `Zzl0ze`, `ngNC2`, `tRARke`, `qJcgMc`, `mrlkwd`, `yBhWQ`, `ve2Lsc`, `DTaVef` |

Mở thông tin ảnh và chọn bộ lọc Hình ảnh không cho thấy URL RPC mới tại thời điểm lấy inventory sau thao tác. Điều này không chứng minh không có request: tài nguyên có thể được cache, inventory có thể gộp URL, hoặc request có thể chưa xuất hiện tại thời điểm đọc.

Không gán tên nghiệp vụ cho từng mã RPC chỉ từ thứ tự tải; có thể có request nền dùng chung.

## reCAPTCHA

Trang project tải `https://www.google.com/recaptcha/enterprise.js` trong document chính. Site key công khai: `6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV`, trùng fallback key trong repo. Anchor iframe dùng origin `https://flow.google.com`. Chưa chứng minh `execute()` trả token hoặc upstream chấp nhận token.

## Đối chiếu với repo

- Session hiện vẫn dùng `https://labs.google/fx/api/auth/session`.
- Project hiện vẫn dùng `https://labs.google/fx/api/trpc/project.createProject` và `project.searchUserProjects`.
- Upload/generation/poll hiện vẫn dùng `https://aisandbox-pa.googleapis.com/v1/...`.

Chưa đủ bằng chứng để thay các endpoint này bằng `batchexecute`. Bước tiếp theo cần capture có request/response body đã lọc bí mật cho từng thao tác, xác định schema và auth trong browser, rồi kiểm thử connector. Cookie và token tiếp tục thuộc Chrome, không chuyển về MCP hoặc Provider.

## Lượt bổ sung: tạo project và thử tạo ảnh

Sau quan sát chỉ đọc ở trên, người dùng yêu cầu tạo project và ảnh thử.

- Đã tạo project thành công và đặt tên `FlowProvider RPC Test 2026-09-07`.
- Project ID: `ba107792-8075-4b62-afc1-f3e4a896fde8`.
- Sau bấm Dự án mới, inventory ghi nhận mã mới `jHPbke`, cùng các request tải project thông thường. Đây là ứng viên RPC tạo project, chưa có body để chứng minh ánh xạ.
- Đã chọn Hình ảnh, Nano Banana Pro, 9:16, x1. UI hiển thị 0 tín dụng.
- Prompt thử: `A red ceramic teapot on a white table, soft daylight, clean studio product photograph, no text.`
- Đã bấm Bắt đầu tạo đúng một lần. Sau kiểm tra giao diện, console và chờ thêm, chưa có job/ảnh mới hiển thị; prompt vẫn còn trong ô nhập. Không có lỗi console trong dữ liệu log công cụ cung cấp. Không gửi lại.
- Chênh lệch inventory so với trước lần gửi chứa RPC `DTaVef`, `nzlxg`; baseline được lấy từ lúc chuẩn bị prompt nên các request này có thể là request nền trong lúc chờ. Không gán chúng là RPC generation.
- Chưa quan sát endpoint generation riêng hoặc request `aisandbox-pa.googleapis.com` trong chênh lệch đó. Chưa xác định yêu cầu đã được backend chấp nhận hay CAPTCHA đã thực thi/thành công.

Kết quả: tạo project đã xác minh; tạo ảnh chưa thành công/không rõ tiếp nhận. Cần network trace có body/status để phân biệt lỗi UI, auth, CAPTCHA và generation; inventory tài nguyên không đủ chẩn đoán.

## Lượt bổ sung sau khi người dùng trực tiếp bấm tạo

Người dùng báo đã bấm Bắt đầu tạo trên project test. Giao diện được xác minh đang chạy 88%, sau đó xuất hiện ảnh `Red ceramic teapot on table`. Như vậy tạo ảnh qua giao diện Flow mới đã thành công.

So với inventory cuối của lần thử trước, quan sát thấy `nzlxg` lặp lại trong lúc chờ; khi ảnh hoàn tất, xuất hiện thêm `ogiZ0b`, `WuwhI`, `ngNC2` tại cùng endpoint `batchexecute`. `ogiZ0b` là ứng viên RPC generation vì xuất hiện trong luồng tạo ảnh này và chưa thấy ở các lần tải trang trước. Chưa có body/status nên không khẳng định ánh xạ hoặc cấu trúc request.

Không thấy URL `aisandbox-pa.googleapis.com` mới trong phần chênh lệch inventory này. Điều này không chứng minh Google không gọi dịch vụ đó ở backend hoặc qua cơ chế không được inventory cung cấp.

Kết quả này xác minh giao diện Google Flow tạo được ảnh trên domain mới; **không xác minh RPC INJECT_RECAPTCHA hoặc generation qua extension FlowProvider**, vì thao tác thành công được thực hiện trực tiếp trên giao diện Google bởi người dùng.
