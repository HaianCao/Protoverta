# P14 — Packaging và nghiệm thu cục bộ

Ngày: 04/11/2026. **COMPLETE trong phạm vi nghiệm thu cục bộ bên dưới; chưa
commit, push hay phát hành.** P14 không chứng nhận toàn bộ chuẩn API, mọi model,
mọi phiên bản harness, hoặc một lần chạy CI remote.

## Đối chiếu tiến độ

Handoff cũ dừng ở P07 chưa commit đã lỗi thời. Mã thực tế:

| Repo | Nhánh | HEAD được thử |
|---|---|---|
| Protoverta | main | `52b201c` + correction chưa commit |
| CodexApter | integration/protoverta | `e757fd3` |
| ClaudApter | integration/protoverta | `1d727fa` |

P08/P09 đã commit ở cả ba repo; core P10–P13 cũng đã commit. P13 có code cho
candidate SDK mode nhưng **chưa có bằng chứng chạy SDK mới trong job riêng**;
không đánh dấu gate đó pass chỉ vì có cờ lệnh. Full-schema OpenResponses vẫn
`FAILED_FULL_SCHEMA`, 0 passed / 12 failed / 0 skipped theo báo cáo P13.

Correction có sẵn trước đợt này được giữ nguyên và xác minh: Responses SSE
`param` không được mang HTTP route; `param: null`, `path` giữ route riêng. Test
refusal được cập nhật theo taxonomy `policy_refusal` đã triển khai ở P09.

## Kết quả mới của đợt này

Windows, Python 3.13.9; baseline SDK OpenAI 3.37.0 / Anthropic 2.3.0;
CLI Codex 0.166.0 / Claude Code 2.1.308. Toàn bộ request acceptance dùng fixture
loopback và key tổng hợp. Không gọi provider thật.

| Gate | Kết quả và phạm vi |
|---|---|
| Toàn bộ source tests Protoverta | 2.146 passed, 320,60 giây |
| Toàn bộ source tests CodexApter | 394 passed + 12 subtests, 310,47 giây |
| Toàn bộ source tests ClaudApter | 426 passed, 311,73 giây; 5 cảnh báo compatibility bảo thủ ở fixture phiên bản chưa xác minh |
| Ruff ba repo | PASS; core kiểm tra cả SDK/property/scripts |
| Strict types | PASS: 6 module runtime, fixture positive và đúng 3 lỗi negative dự kiến; không phải toàn bộ codebase |
| Build ba wheel | PASS; byte trong package/asset khớp source, kiểm tra không đóng gói .env/log/session/cache |
| Import sau cài wheel | Cả ba package lấy từ site-packages của venv mới, không editable checkout |
| pip check | PASS sau khi hoàn tất dependency kiểm thử |
| Official SDK trên wheel | 29 passed, 65,26 giây; không skip |
| Property gate trên wheel | 24 passed, 15,63 giây; 50 ví dụ/test, Hypothesis 6.170.9 |
| CLI thật + launcher đã cài | 6/6 PASS: mỗi CLI qua chat/messages/responses; mỗi ca 2 vòng request, 2 tool results, exit 0 |
| Browser trên wheel | Cả hai PASS: light/dark, mobile/desktop, keyboard, save/reload, manual model, error recovery, agent policy, Run |
| Foreground Run | Launcher/gateway thật + CLI child tổng hợp; setup đóng, child tiếp tục, exit code 7 truyền đúng; không phải PTY native đầy đủ |
| Windows lifecycle | Install/update/uninstall cả hai PASS; config sentinel còn nguyên |
| Shell/path | CMD + PowerShell, đường dẫn có dấu cách, cwd ngoài repo, full executable path và bare command qua PATH chỉ của child |

Các tool loop dùng model fixture, nên cảnh báo model metadata không có trong
catalog native là dự kiến; không phải bằng chứng về metadata của model thật.
Không chạy lại subagent/compaction/resume live trong đợt P14; kết quả P05–P08
có báo cáo riêng và giữ nguyên phạm vi phiên bản của chúng.

## Cách cô lập và dấu vết

Preview mới: `p14-reconcile-20261104-d7b6c6f5` dưới
`%LOCALAPPDATA%\ProtovertaPreviews`. Artifact cũ trong P00 và `p14 wheel preview`
không bị ghi đè. Có `manifest.json`, `source-snapshot.json`, `dist/`, `reports/`
và screenshot hai ứng dụng trong preview mới.

Ba wheel được build từ source local. Test dependency được sao chép từ venv
preview có sẵn; không sao chép ba adapter package hay editable `.pth` vào venv
mới. Khi tải dependency gặp thiếu `sortedcontainers`, dùng bản sao 2.4.0 có sẵn
và cài Hypothesis được pin; `pip check` cuối cùng pass. Đây không phải một ca
chứng minh mọi dependency đều tải được từ Internet trong môi trường mới trắng.

| Wheel | SHA-256 | Số file package/asset đã đối chiếu |
|---|---|---|
| protoverta-0.3.0a8 | `07d1041b4b76e79277d8440e71684e7bff1dd106430264c4e9b1ccdd0fe2d321` | 43 |
| codexapter-0.2.0a2 | `531e3b02e48a161fa91fbbc05ba30741053d8dd57d970306524554ba5a3f2530` | 27 |
| claudapter-0.2.0a2 | `fa45ce17b53fae73120da1adc05500659d4d32647e42b801c362beb7b68ba5ba` | 24 |

HEAD không đủ định danh correction chưa commit. Runtime hash source tương ứng:

- Core: `e079f8c70632a8b8d237c645d0449dac52b2d3578c99b8d7d461a8eb8394886d`.
- Codex: `0a9725d69b2dd2dccf54d1c8b5288094d285c85e6498b70278531e94ce094ff0`.
- Claude: `821680bfa6f1bd08819649648c90059fe6f407ca00894562c6eb5f58a2631aef`.

Installer được chạy với `-SkipCodexInstall` / `-SkipClaudeInstall`,
`-NonInteractive`, `-NoPathChanges`, `-ProtovertaWheel` và các thư mục
APPDATA/LOCALAPPDATA/USERPROFILE riêng. Không chạy uninstall reset môi trường
thật, không sửa native CLI/PATH/config/session/key hoặc `free-claude-code`.

Đã mở screenshot desktop Codex và mobile Claude để xem bố cục; automation
kiểm tra thêm hai theme, logo/favicon và không tràn ngang. Nội dung lỗi
`Failed to fetch` trong ảnh là ca abort request chủ động; provider có `<b>` được
hiển thị nguyên văn để kiểm chứng escaping.

## Bổ sung cho kiểm tra shell

`scripts/smoke_windows_install.py` hiện kiểm tra cả lệnh có đường dẫn đầy đủ
và tên lệnh đứng đầu PATH của child. `Get-Command -CommandType Application`
có thể trả nhiều kết quả; test chọn kết quả đầu tiên để so với executable
preview, rồi chạy lệnh đó. Trước khi sửa phép so sánh danh sách này, ca test
PowerShell báo lỗi dù wrapper preview chạy được; đã tái hiện, sửa và chạy lại
toàn bộ lifecycle thành công. Đây là correction của test, không phải đổi PATH
production hay đổi cách launcher chạy.

## Tái hiện

Theo [local preview](local-preview.md) để tạo venv/wheel/state riêng, không
activate vào môi trường đang dùng. Dùng Python đầy đủ đường dẫn của preview:

```powershell
& $previewPython "$workspace\protoverta\scripts\check_sdk.py" --installed
& $previewPython "$workspace\protoverta\scripts\check_fuzz.py" --installed
foreach ($client in @("codex", "claude")) {
    foreach ($protocol in @("chat", "messages", "responses")) {
        & $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client $client --upstream $protocol --installed-adapter --tool-loop
        if ($LASTEXITCODE -ne 0) { throw "CLI smoke failed: $client/$protocol" }
    }
}
```

Browser: `browser_setup_smoke.py --source PATH --package codex_adapter|claudapter
--installed --launch-fixture`; dùng Chromium test đã cài. Lifecycle local:
`smoke_windows_install.py --wheel PATH_TO_LOCAL_CORE_WHEEL`; không dùng
`--remote` để chứng minh source chưa push. Cần cài các extra test được pin
tương ứng; các gate có dependency thiếu phải fail/pending, không âm thầm skip.

## Gate còn lại và giới hạn

- P13: job/evidence cho SDK candidate mới, tách baseline pin, còn pending.
- Remote CI/Linux/Python khác và installer remote không được nghiệm thu bởi
  đợt Windows local này. Chưa push/release nên chưa xác nhận các artifact remote.
- Full-schema OpenResponses chưa đạt; không bịa usage, sampling default hay
  field không có nguồn chỉ để làm xanh validator.
- Reasoning signed/encrypted state, hosted tool/server state không có tương
  đương vẫn bị từ chối theo strict contract; không thêm fallback mid-turn.
- Codex closed custom-child resume còn giới hạn đã công bố. Các session độc lập
  không đồng nghĩa nhiều provider tùy ý trong cùng cây agent hay detached agent.
- Live search/MCP Internet vẫn pending lựa chọn và kiểm chứng riêng. Gateway
  không thực thi tool hoặc tự cung cấp khả năng truy cập mạng cho harness.
- Không có lần live provider mới; báo cáo provider cũ không tự được nâng thành
  chứng nhận cho build này. Đợt soak cũ không được ghi thành soak mới.

P14 hoàn thành kiểm chứng packaging/preview trong scope trên; chưa có kết luận
“mọi API đã tương thích” hoặc “toàn bộ kế hoạch không còn gate pending”.
