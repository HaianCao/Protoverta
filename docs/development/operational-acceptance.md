# Kiểm chứng vận hành trước phát hành

Các gate dưới đây độc lập: unit test, SDK parser, full schema, vận hành dài hạn
và provider/harness thật không thay thế cho nhau. Không đổi kết quả thiếu bằng
chứng thành pass. Không nới strict reasoning để làm test xanh.

## Soak có thời lượng bắt buộc

`soak_local.py` mặc định vẫn là bài **bounded**: có thể dừng khi đủ request trước
khi hết thời gian. Dùng `--sustained` để yêu cầu chạy đủ thời lượng; hết budget
request sớm sẽ làm `passed=false` và exit 1. Chế độ này giới hạn tối đa 4 giờ,
100.000 request, 16 worker; không phải bài tải vô hạn.

Chạy từ checkout Protoverta, trong venv kiểm chứng riêng đã cài extra `soak-test`:

```powershell
# Kiểm tra nhanh trước, không gọi provider hay cần key thật.
python scripts/soak_local.py --sustained --seconds 120 --requests 1000 --concurrency 2 --interval 1 --measure-resources --report quick-soak.json
if ($LASTEXITCODE -ne 0) { throw 'Soak nhanh chưa đạt; đọc report' }

# Gate hai giờ, chạy khi máy có thể hoạt động liên tục trong khoảng này.
python scripts/soak_local.py --sustained --seconds 7200 --requests 15000 --concurrency 2 --interval 1 --measure-resources --report two-hour-soak.json
if ($LASTEXITCODE -ne 0) { throw 'Soak hai giờ chưa đạt; đọc report' }
```

Không đặt `--installed` nếu muốn đo source checkout hiện tại. Nếu dùng flag này,
cần kiểm tra phiên bản/wheel thực sự đã cài riêng; script không tự build hay cài
đè. Chọn đường dẫn report mới để giữ bằng chứng của từng lượt chạy. CLI in
heartbeat JSON ra stderr khoảng mỗi 30 giây và report cuối ra stdout/file.
Kill tiến trình không tạo một report pass; nếu thiếu report cuối thì chưa đạt.

Kiểm tra ít nhất:

- `passed=true`, `mode=sustained`, `duration_reached=true` và
  `workload_seconds` không nhỏ hơn thời lượng yêu cầu.
- Không có `failure_types`; `counts.failed_clients` bằng 0 (key vắng cũng tương
  đương 0). Cancel cố ý và stream cắt sớm phải kết thúc đúng lỗi đã thiết kế,
  không tạo completed giả; đây không phải upstream thành công.
- `opened=closed`, `active_after=0`, `lifetime_threads_after=0`.
- Resource counters khả dụng và `policy_passed=true`: RSS sau warmup tăng không
  quá 64 MiB, thread giữ lại không quá 2, handle/fd không quá 4, TCP không vượt
  baseline. Đây là ngưỡng quan sát, không phải bằng chứng không có mọi memory leak.

Bài này giữ hai server gateway đồng thời, dùng key/tag tổng hợp khác nhau,
request JSON/SSE, client ngắt và stream thiếu kết thúc. Nó chỉ chạy Chat upstream
sang Responses/Messages bằng injected transport; không chứng nhận toàn bộ sáu
route, provider thật, hosted search hay native CLI. Report tài nguyên giữ tối đa
605 mẫu gần nhất; baseline đầu, warm baseline, terminal và peak toàn kỳ vẫn được
giữ riêng. `sample_count` là tổng số mẫu, không phải số mẫu còn trong bộ đệm.

Workflow thủ công `Ecosystem integration` có input `soak_seconds=300` hoặc `7200`.
Budget request và nhịp gọi được chọn để không kết thúc sớm do hết lượt. Workflow
chỉ dùng loopback fixtures; cần các commit đã có trên GitHub trước khi dispatch.
Thời gian job được tăng để bao cả source/wheel suites và soak đã chọn. Việc thêm
cấu hình này **không** có nghĩa đã dispatch hay chạy CI remote/hai giờ.

## Search/fetch Internet thật — pending

Fixture MCP chỉ chứng minh harness gọi tool và nhận result. Để chấp nhận Internet
thật, người dùng cần chọn một MCP server đã cài, cung cấp executable tuyệt đối
và tên biến môi trường cần thiết, không gửi key trong chat hay args.

Dùng profile/config/session tạm như [local-preview.md](local-preview.md) và
cấu hình MCP riêng từng phiên theo [harness-tools.md](harness-tools.md). Không
đăng ký MCP toàn cục, tự cài server, mở sandbox/proxy hay lấy key provider đưa
cho MCP khác. Chạy một truy vấn tổng hợp vô hại, xác nhận tool result có URL,
nội dung thực và model dùng đúng result. Kiểm tra cả hai harness với routes đã
được xác nhận; ghi CLI/model/protocol/MCP version và thời gian, không ghi key.

## Các giới hạn tiếp tục giữ nguyên

- Signed/encrypted reasoning không có tương đương phải bị từ chối; không thêm
  chữ ký giả hay nhét reasoning vào answer text để vượt contract.
- Resume transcript giữa provider không chứng minh giữ được reasoning state,
  resource ID hay opaque reference. Test dùng session tạm, không sửa session thật.
- Auto reasoning chọn policy theo protocol/endpoint, không bảo đảm mọi model
  tuân thủ yêu cầu tắt; không fallback/retry turn tool khi provider trả dữ liệu
  không tương đương.
- Full OpenResponses schema vẫn có gate riêng đang thất bại; xem
  [audit](openresponses-audit.md). Một bài soak pass không làm schema gate pass.
- Kiểm chứng artifact phát hành, CI remote và interactive terminal vẫn cần
  bằng chứng riêng; không tự push/merge/release để hoàn tất checklist này.

## Trạng thái ngày 2026-10-28

Đã bổ sung sustained mode, bảo vệ bộ đệm đo tài nguyên dài hạn và regression.
Full core 1673 test pass, gồm 14 regression mới; Ruff và whitespace pass.
Hai bài 120 giây với đo tài nguyên pass. Lượt cuối: 120,767 giây workload,
222 request, 201 upstream response mở/đóng, không có failure hay lifetime thread
còn lại. Warm RSS giảm 1.400.832 byte, retained handle +2 và TCP delta 0, đạt
ngưỡng đã công bố. Report `%TEMP%/protoverta-k4-sustained-final-20261028.json`.
Source consumer và bản cài thật không thay đổi trong bước này. YAML workflow đã
parse, chưa dispatch. **Chưa có kết quả soak hai giờ mới**,
chưa có acceptance search Internet thật do người dùng chọn. Vì vậy K4 là
**PARTIAL**, không phải hoàn tất chứng nhận vận hành.
