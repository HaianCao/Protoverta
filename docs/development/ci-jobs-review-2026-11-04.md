# Rà soát job CI sau P14 — 04/11/2026

P14 đã commit ở `decee93`. Các thay đổi CI dưới đây được thực hiện sau commit
đó và **chưa commit/push**. Launcher vẫn ở `integration/protoverta`; không merge
vào main, không dispatch CI hoặc phát hành.

Đợt [sửa snapshot Responses](response-snapshot-fixes-2026-11-04.md) diễn ra sau
bản rà soát này và có thay đổi runtime core. Các dòng “runtime không đổi” bên
dưới chỉ mô tả đúng checkpoint CI trước đợt sửa snapshot, không phải source mới.

## Lỗi và khoảng trống đã sửa

| Phát hiện | Thay đổi |
|---|---|
| Test snapshot P13 phụ thuộc repo launcher nằm cạnh core, gây lỗi khi CI chỉ checkout Protoverta | Dùng ba project fixture và Git output tổng hợp; kiểm thử lại trong bản sao chỉ có core, không có hai repo bên cạnh |
| P13 có cờ candidate SDK nhưng không có job đánh giá riêng | Thêm `sdk-candidates.yml`, dispatch thủ công/lịch hàng tuần, Ubuntu và Windows; nâng SDK ổn định trong runner riêng, không thay baseline pin |
| Runner SDK chỉ cần một testcase pass cũng có thể xanh nếu suite bị rút thiếu | Bắt buộc đủ 29 case theo inventory hiện tại, không skip/failure/error; cập nhật inventory phải có chủ đích khi thay suite |
| JUnit SDK bị xóa khi kết thúc, thiếu bằng chứng phiên bản thực tế | `check_sdk.py --report-dir NEW_PATH` lưu XML và JSON baseline/actual SDK, platform, số case, verdict, installed/candidate mode và version drift; giữ evidence FAILED khi lỗi version hoặc subprocess |
| Candidate mode chạy đúng baseline có thể bị hiểu nhầm là đã thử SDK mới | JSON ghi `sdk_version_drift`; runner cảnh báo rõ khi không có drift. Job lưu thêm outcome từng stage ngay cả khi cài dependency thất bại |
| SDK/property CI chỉ thử editable source | Build wheel, thay editable bằng wheel và chạy `--installed`; SDK giữ artifact riêng từng OS/Python |
| Release chỉ test Linux source trước build, không đợi matrix CI hay thử wheel phát hành | Release gọi reusable CI, build chờ toàn bộ job đó; wheel build ra phải qua pip check + SDK/property installed trước publish |
| Job launcher chỉ chặn skip ở vài tên file gateway | Thêm JUnit validator độc lập cho cả hai repo: gateway=true không cho phép skip ở bất kỳ file nào; report rỗng/toàn skip/failure/error đều fail. Direct-only cho phép optional gateway skip nhưng phải có case thực sự chạy |
| Chạy direct-only thật phát hiện 7 test Codex và 23 test Claude thất bại do giả định core luôn được cài | Chỉ các case gateway có prerequisite `importorskip` rõ ràng; case cache identity direct không còn patch module core không dùng. Giữ test direct độc lập và kiểm lại chính các file đó với core + strict no-skip, không bỏ cả file hoặc giảm coverage gateway |
| Plugin integration bỏ lọt importorskip API mới hoặc skip ở module scope | Kiểm tra lý do skip liên quan Protoverta, bắt cả collection và runtime; vẫn giữ các skip platform không liên quan ở core |
| CI launcher chỉ có Python 3.11; ClaudApter thiếu parse PowerShell 5.1 | Matrix 3.11/3.13 × direct/gateway, tên artifact không trùng; parse PowerShell 5.1 và 7 |
| Workflow chưa có linter kiểm tra context/expression/reusable jobs | Thêm actionlint 1.7.12 vào CI cả ba repo, chạy bằng tool Go pin; giới hạn timeout riêng |
| Ecosystem nhận branch dù input được mô tả là immutable commit | Bắt buộc full SHA 40 hex trước checkout, truyền input qua env thay vì ghép script |
| Ecosystem dùng Codex 0.164.0, chỉ tool loop Chat, browser stub launcher | Pin 0.166.0, chạy hai CLI qua cả ba protocol; browser dùng launcher/gateway thật và CLI child tổng hợp với `--launch-fixture` |
| Ecosystem còn lựa chọn soak hai giờ không khớp thời lượng người dùng chọn | Chỉ 300 hoặc 1.800 giây; cập nhật tài liệu vận hành. Đợt này không tự chạy soak mới |
| Driver ecosystem thiếu report source/installed bền vững và kiểm tra origin | Ghi JUnit riêng từng repo/stage, kiểm tra JUnit launcher và pip check, xác nhận import từ site-packages, giữ SDK report và upload reports; thêm timeout 900 giây mỗi subprocess |

`check_test_report.py` được giữ độc lập trong mỗi repo để CI launcher không cần
phụ thuộc core chỉ để đọc XML. Driver ecosystem dùng bản của chính core, nên
không buộc các commit launcher cũ phải có thêm helper CI mới.

## Kiểm chứng đã thực hiện

Windows / Python 3.13.9, state/venv riêng, không gọi provider thật:

- Full suite workspace: core **2.158 passed**, Codex **400 passed + 12 subtests**,
  Claude **432 passed**, không skip. Claude có 5 cảnh báo fixture version chưa
  xác minh như đợt P14. Đã đưa JUnit thật của ba suite qua validator mới: PASS.
- Bản sao core độc lập, chỉ có source/scripts/tests/docs của Protoverta, không
  `.git`, `.env`, hai launcher hay bản cài thật: **2.164 passed**, 325,87 giây.
  Lượt này bổ sung sáu case JUnit guard so với suite workspace phía trên.
- Sau bước chốt inventory SDK, chạy lại nhóm gate liên quan: **37 passed**,
  gồm SDK evidence/version/relative report path, cấm dùng lại artifact cũ,
  suite thiếu case, skip/failure/error, API thiếu ở collection/runtime, snapshot
  fixture và JUnit. Không gộp các lượt này thành một full suite mới 2.166 case.
- Direct-only trong venv thực sự không có Protoverta: sau lần đầu phát hiện
  7/23 lỗi giả định dependency, đã sửa và chạy lại toàn bộ. Codex **362 passed,
  38 skipped, 12 subtests**; Claude **389 passed, 43 skipped**. JUnit gate
  `--allow-skips` PASS; skip là phần cần core tùy chọn. Hai wheel launcher local
  được cài không kèm core, pip check PASS; xác minh `find_spec('protoverta') is
  None` trước suite. Source tests vẫn dùng source local; wheel cung cấp package
  metadata/dependency boundary cho venv này.
- Kiểm lại các file vừa phân loại với core wheel và plugin/JUnit strict:
  Codex **39 passed**, Claude **76 passed**, **không skip**. Các skip ở direct-only
  không thể che API core thiếu trong nhánh gateway=true.
- SDK thật trên wheel: **29 passed** với runner cuối cùng, 27,73 giây; baseline
  OpenAI 3.37.0 / Anthropic 2.3.0. Artifact `ci-sdk-baseline-final/` ghi
  `expected_testcases=29`, `testcases=29`, `outcome=PASSED` và không có SDK drift.
- Actionlint **1.7.12**: toàn bộ YAML của ba repo PASS. Binary lấy từ release
  chính thức, đối chiếu SHA-256 với checksums trước chạy; không cài vào PATH.
  Lượt local tắt tích hợp shellcheck/pyflakes vì chưa cài hai tool đó; CI Ubuntu
  chạy actionlint bình thường. Actionlint không thực thi job hoặc chứng minh
  dependency/runner remote chạy thành công.
- Ruff ba repo PASS. Tất cả thay đổi mới thuộc workflow, scripts test và test;
  runtime protocol/launcher không đổi sau `decee93`.
- Parser Windows PowerShell **5.1.26100.9711**: sáu script cài/gỡ PASS. Chỉ dùng
  ExecutionPolicy Bypass cho tiến trình parser đọc AST; không đổi policy lưu
  trên máy hoặc thực thi installer. Máy local không có `pwsh`; job parse
  PowerShell 7 đã thêm nhưng kết quả chạy parser 7 vẫn chờ runner CI.

Artifact local ở preview `p14-reconcile-20261104-d7b6c6f5/reports`: các file
`ci-source-*.xml`, `ci-core-standalone.xml`, `ci-installed-sdk-final.log`,
`ci-sdk-baseline-final/`, `ci-actionlint-*.log`, `ci-junit-*.log`.
Lượt direct đầu giữ ở `ci-direct-*.xml/log`; kết quả cuối ở
`ci-direct-final-*.xml/log`, cùng `ci-gateway-regression-*.xml/log`.
Không ghi đè artifact nghiệm thu P14 trước đó.

## Những gì vẫn chưa được chứng minh

1. **SDK mới thực sự:** đã tạo venv candidate độc lập và thử nâng OpenAI/Anthropic,
   nhưng pip timeout sau 90 giây. Đây là lỗi môi trường/tải dependency, không
   phải kết quả tương thích SDK candidate. Không retry dồn và không dùng baseline
   PASS thay cho kết quả mới. Job riêng đã được thêm; đợi môi trường tải được
   dependency rồi kiểm tra actual versions và version drift trong artifact.
2. **Remote CI:** chưa push hoặc dispatch, chưa chạy Ubuntu/Python 3.11 hay matrix
   mới trên GitHub. Local Windows 3.13 và actionlint không chứng nhận các tổ hợp đó.
   Chưa chạy nguyên job ecosystem/release end-to-end; đã kiểm các thành phần
   local nêu trên. Sáu CLI tool loops/browser/lifecycle có bằng chứng P14 riêng.
3. **Published wheel và release:** launcher gateway jobs vẫn cố ý thử wheel được
   pin từ release. Không được coi version string `0.3.0a8` là bằng chứng byte của
   release giống source hiện tại. Nếu release cũ thiếu API, strict gate phải fail;
   dùng ecosystem với các SHA đã push để kiểm bộ source/wheel khớp. Trước phát
   hành mới cần chọn version và đồng bộ core pin/URL ở hai launcher; không đổi
   validator hoặc nới skip để che lỗi, không tự ghi đè release cũ.
4. **Schema/semantics:** full-schema OpenResponses vẫn FAILED_FULL_SCHEMA.
   Reasoning signed/encrypted state, hosted server state và closed custom-child
   resume không được cải thiện chỉ bằng CI. Không tự chuyển state sang text,
   bịa usage/signature hoặc fallback model giữa turn có tool.
5. **Native UI/subagent/Internet:** các job này không tự chứng nhận mọi lifecycle
   agent, phiên bản CLI tương lai, native PTY picker hay live MCP search/fetch.
   Các gate phiên bản P05–P08 và live-search pending vẫn giữ phạm vi riêng.

## Cách chạy tiếp trên GitHub khi được phép push

CI thường chạy trên push/PR; candidate SDK có dispatch riêng và lịch trên
default branch. Release chỉ chạy khi có tag và quyền publish riêng; việc sửa
workflow không phải lệnh phát hành. Ecosystem cần hai **full commit SHA đã có
trên remote**, chọn soak 300 giây trước; không dùng tên branch làm SHA giả.

Sau mỗi lượt, đọc verdict job, XML, `sdk-evidence.json`, `workflow-stages.json`
và snapshot/hash tương ứng. `sdk_version_drift=false` chỉ xác nhận lại baseline,
không đóng gate SDK mới. Phân biệt dependency/network failure, skip và semantic
failure, không sửa ngưỡng chỉ để đưa về xanh.

Tài liệu tham chiếu: [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[artifact action](https://github.com/actions/upload-artifact),
[actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12).
