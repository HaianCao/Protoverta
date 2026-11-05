# P13 — SDK candidate và ma trận Python 3.11 local (05/11/2026)

Đợt này đóng gate còn PENDING của P13 (thử SDK mới trong job riêng) và chạy
các ô Windows/Python 3.11 của CI ngay trên máy local. Không push, không chạy CI
remote, không gọi provider thật, không sửa bản cài/config/PATH thật.

## Danh tính bản kiểm thử

| Repo | Commit |
|---|---|
| `protoverta` | `e647564` (sửa test bên dưới commit cùng báo cáo này) |
| `free-codex-cli` | `e698b09` (`integration/protoverta`) |
| `free-claude-cli` | `f85e06f` (`integration/protoverta`) |

Wheel core build lại từ source: SHA-256
`aec2525e946810f06ea471def1dc6e3bce0cc848dee6da98d289a26a97bfbbb9`, trùng
wheel nghiệm thu 04/11, tức runtime không đổi. Các suite 3.11 chạy trên bản
clone sạch của HEAD trong `%LOCALAPPDATA%/ProtovertaPreviews/ci311-20261105`.

## Kết quả

| Gate | Python | SDK | Kết quả |
|---|---|---|---|
| SDK candidate (`--installed --allow-candidate-sdk`) | 3.13.9 | openai 3.46.0, anthropic 2.5.0 | **29/29 PASS**, `pip check` sạch, `provider_requests: 0` |
| SDK baseline pin | 3.11.17 | openai 3.37.0, anthropic 2.3.0 | **29/29 PASS** sau khi sửa test (trước đó 28/29, xem dưới) |
| SDK baseline pin | 3.13.9 | openai 3.37.0, anthropic 2.3.0 | **29/29 PASS** với test đã sửa |
| Core source tests | 3.11.17 | — | **2544 tests, 0 fail**, 1 skip (test cần `openai`, không cài ở suite source — đúng thiết kế) |
| Ruff / scoped strict types | 3.11.17 | — | PASS / PASS |
| CodexApter tests | 3.11.17 | — | **412 testcase (400 + 12 subtest), 0 fail** |
| ClaudApter tests | 3.11.17 | — | **432 passed**, 9 subtest |

P13 candidate gate: **PASS** cho candidate trên Windows. Đây là bằng chứng cho
đúng hai phiên bản SDK nêu trên, không phải cho mọi bản SDK tương lai. Baseline
pin trong `pyproject.toml` giữ nguyên; không đổi pin.

## Lỗi flaky phát hiện trên 3.11 và cách sửa

`test_official_sdk_accepts_post_payload_keepalives_before_verified_eof[chat-responses]`
lỗi `assert keepalives` (rỗng) khoảng 1/12 lần chạy trên Python 3.11 Windows.

Nguyên nhân: trên Windows, Python < 3.13 dùng `GetTickCount64()` cho
`time.monotonic` (độ phân giải 15,625 ms); 3.13 dùng `QueryPerformanceCounter()`.
Test giữ EOF bằng `time.sleep(0.12)` cố định và giả định heartbeat interval
0,02 s chắc chắn phát trong cửa sổ đó; với đồng hồ thô cộng tranh chấp GIL/lock
giữa thread handler, heartbeat và SDK client, giả định này đôi khi sai.

Không phải lỗi runtime: production interval là 15 s, sai số 15 ms không đáng kể;
`tests/test_stream_heartbeat.py` dùng đồng hồ giả lập và vẫn đạt.

Sửa (chỉ test): EOF được giữ đến khi một keepalive **đã ghi xong lên socket**
(`threading.Event`), có giới hạn 2 s để heartbeat bị thiếu vẫn là lỗi. Khẳng
định của test không đổi: keepalive sau payload, trước EOF đã xác minh, được
SDK chính thức chấp nhận. Sau sửa: 30/30 lần lặp đạt trên 3.11.

## Những gì vẫn chưa làm được ở local

- **Ubuntu**: WSL `Hami-Ubuntu` 24.04 có Python 3.12 nhưng không có `venv`/`pip`
  (cần `sudo apt install python3.12-venv` hoặc cài uv). Chưa cài gì vào WSL.
- **actionlint**, CI/release remote: không chạy (không push).
- **OpenResponses full-schema**: vẫn 0/12, 78 lượt. Toàn bộ field còn thiếu là dữ
  kiện upstream/provider (sampling mặc định, service tier, reasoning subtotal của
  Messages, penalty/top_logprobs). Điền vào sẽ là số giả, trái nguyên tắc 3.
- **Live logprobs** và **UI/PTY với provider thật**: cần provider/model và quota
  do người dùng chọn.

## Tái hiện

```powershell
$R = "$env:LOCALAPPDATA\ProtovertaPreviews\p13-candidate-20261105"
& "$R\venv\Scripts\python.exe" scripts\check_sdk.py --installed --allow-candidate-sdk --report-dir NEW_DIR
```

Evidence JSON: `p13-candidate-20261105/sdk-candidate-report{,-fixed}/sdk-evidence.json`,
`ci311-20261105/sdk-baseline-311-fixed`, `sdk-baseline-313-fixed`, `core-311.xml`,
`codex-311.xml`.
