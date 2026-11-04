# Kiểm chứng local và preview — 04/11/2026

Đợt này chỉ commit và test local theo yêu cầu người dùng. Không push nhánh kiểm
thử, không chạy workflow remote, không phát hành hay merge hai nhánh integration
vào main. Bản cài, config, session và PATH thật được giữ nguyên.

## Các phần đã thực hiện

| Phần | Kết quả / commit |
|---|---|
| Generation controls và logprobs có kiểm tra token/byte | `af89a98` |
| Probe logprobs có giới hạn, ghi nhận provider chưa đạt | `fcb6690` |
| Sampling profile cho ba chuẩn; penalty dialect Responses khai báo tường minh | `cb9aa2f` |
| Kiểm chứng packaging, CLI, lifecycle và hướng dẫn preview | Báo cáo hiện tại |

Chi tiết code/test: [generation profiles](generation-profiles-2026-11-04.md).
Nguồn test local đạt: **2544 core, 400 Codex +12 subtest, 432 Claude**.
SDK đã cài đạt **29**, property đạt **24 × 50** ví dụ. Sáu vòng tool CLI thật
với upstream loopback giả lập đạt. Đây không phải sáu phép thử provider thật.

Kiểm tra bổ sung trên wheel đã cài đạt **55 test / 12,35 giây** cho generation
profile và probe. Chạy ngoài repo, tắt cấu hình source `pythonpath`, import runtime
từ site-packages; riêng helper probe được nạp từ thư mục scripts để test công cụ.

## Cài → cập nhật → gỡ bỏ trên Windows

Lệnh đã dùng với Python của preview, không dùng `--remote`:

```powershell
& $previewPython "$workspace\protoverta\scripts\smoke_windows_install.py" --wheel "$previewRoot\dist\protoverta-0.3.0a8-py3-none-any.whl"
```

Kết quả **cả CodexApter và ClaudApter đều đạt**:

- Cài từ source integration local và wheel Protoverta local, sau đó chạy installer
  lần hai để kiểm tra cập nhật và đúng phiên bản dependency.
- Gọi `--help` bằng đường dẫn tuyệt đối và tên lệnh trong CMD/PowerShell từ một
  thư mục không thuộc repo. PATH chỉ thay trong tiến trình con.
- Thư mục cài chứa dấu cách; config sentinel giả lập không bị thay khi cập nhật.
- Uninstall gỡ wrapper, giữ config sentinel. Không gỡ CLI Codex/Claude thật.

Script dùng APPDATA, LOCALAPPDATA, USERPROFILE, HOME riêng; truyền
`-NonInteractive -NoPathChanges` và `-SkipCodexInstall` / `-SkipClaudeInstall`.
Không truyền `-ResetEnvironment`, không chạy pipx uninstall. Thư mục lifecycle
tạm được script dọn sau khi thành công. Venv preview dùng cho kiểm tra wheel
vẫn được giữ để xem log, không có nghĩa installer thật đã được cập nhật.

## Packaging và phạm vi CI local

Ba wheel khớp byte runtime của source. Ba sdist build thành công; Twine kiểm tra
**6/6 artifact đạt**. Kiểm tra danh sách entry không thấy file `.env` riêng, pyc
hay file `.coverage`. `pip check` đạt trước và sau khi bổ sung công cụ packaging.
Các công cụ build/Twine chỉ được cài vào venv preview riêng.

Lần chạy packaging đầu chưa có module `build` trong Python kiểm tra; đã cài công
cụ vào venv preview rồi chạy lại thành công. Không đổi dependency runtime để sửa
môi trường test. Ruff phải chạy từ thư mục từng dự án để phân loại import đúng;
lần chạy cuối đúng cwd đạt trên toàn bộ đường dẫn core CI và hai launcher.

| Job / gate | Phạm vi thực sự đã kiểm tra |
|---|---|
| Source tests, Ruff, scoped strict types | Windows, Python 3.13 |
| Wheel + SDK + property | Venv mới, dependency SDK đã ghim |
| Real CLI tool loop | Codex 0.166.0 và Claude Code 2.1.308, loopback |
| Installer lifecycle | Hai launcher, source local; CMD và PowerShell |
| Ubuntu, Python 3.11, actionlint trên runner | Chưa chạy trong đợt local này |
| GitHub CI / release / tải cài từ remote | Không chạy theo lựa chọn người dùng |

Không thể xem kết quả trên Windows/Python 3.13 là bằng chứng cho toàn bộ ma trận
CI. Không có thay đổi workflow nên không tự đẩy code để lấy một kết quả remote.

## Những giới hạn còn nguyên

1. Audit official không sửa schema/fixture vẫn **0/12 đạt, 78 lượt thiếu field**.
   Mặc định không khai sampling, penalty, top-k hay tier không có bằng chứng.
2. Bốn request logprobs thật không chứng nhận được hai route đã thử: OpenRouter
   không trả records, NIM trả reasoning/probabilities nhưng hết cap trước text.
   Xem [báo cáo live](live-logprobs-2026-11-04.md). Không tăng budget hay retry.
3. Reasoning có chữ ký/mã hóa, state khác protocol và các API ngoài subset vẫn
   giữ giới hạn đã công bố. Không bỏ reasoning hay tạo chữ ký giả.
4. Chấp nhận giao diện/PTY tương tác với provider người dùng chọn vẫn là bước
   test thủ công. Không tự mở agent hay thay bản đang dùng để kiểm tra.

## Thử trên máy của bạn

Dùng [local-preview.md](local-preview.md), tạo preview **mới** theo source vừa
commit; preview cũ không tự nhận code mới. Không cần installer GitHub hoặc sửa
PATH. Thử ping → tạo/đọc/sửa file trong workspace tạm → gọi tool nhiều vòng → thoát
và resume theo route đã hỗ trợ. So sánh model/provider thực dùng, tránh suy diễn
phản hồi “thành công” của model thành bằng chứng một tool đã chạy.

Generation profile chỉ dành cho người vận hành có bằng chứng tương ứng; không
cần tạo profile để chạy thử thông thường. Không bật penalty Responses vì muốn
làm xanh schema. Kết thúc thì đóng terminal preview; bản stable trên PATH vẫn
như trước. Live search/MCP và tương tác UI thật là kiểm tra riêng.

## Dữ liệu tái hiện

Root local: `%LOCALAPPDATA%/ProtovertaPreviews/snapshot-20261104-generation-final`.
Có manifest, source XML/log, SDK/property/type/CLI log, audit JSON, kiểm tra
installed-generation, ba log sdist, Twine và artifact-hygiene JSON. Wheel core
SHA-256: `aec2525e946810f06ea471def1dc6e3bce0cc848dee6da98d289a26a97bfbbb9`.
Không commit venv, raw session hay credential. Lifecycle kết quả được ghi từ
stdout của script; không khẳng định có file raw log lifecycle trong root này.
