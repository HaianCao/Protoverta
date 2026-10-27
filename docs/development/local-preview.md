# Chạy thử bản mới mà không thay thế bản đang dùng

Hướng dẫn dành cho Windows, dùng PowerShell 5.1 hoặc PowerShell 7. Bạn sẽ build
ba dự án và cài vào **venv riêng trong thư mục preview**, không cài đè wrapper hiện
tại, không cần activate venv và không thêm PATH.

Hai launcher phải ở nhánh **integration/protoverta**; bản main ổn định của chúng
chưa có tích hợp này. Các bước dùng code local, không yêu cầu push hoặc phát
hành package trước.

Làm lần lượt trong **cùng một cửa sổ PowerShell mới** để giữ các biến
$workspace, $previewRoot và $previewPython. Nếu một bước lỗi, dừng tại đó.

## 1. Kiểm tra đúng thư mục và nhánh

Cần Python 3.11 trở lên, Git, Codex CLI và Claude Code CLI đã cài.
Lần thử gần nhất dùng Python 3.13.9, Codex CLI 0.164.0 và Claude Code 2.1.308;
phiên bản CLI khác có thể thay đổi cờ lệnh.

Mở terminal ở thư mục **chứa cả ba repo**:

~~~text
free_agent/
├── protoverta/
├── free-codex-cli/
└── free-claude-cli/
~~~

Nếu terminal đang ở free-codex-cli, chạy **Set-Location ..** trước. Sau đó:

~~~powershell
$workspace = (Get-Location).Path
foreach ($repo in @("protoverta", "free-codex-cli", "free-claude-cli")) {
    if (-not (Test-Path -LiteralPath "$workspace\$repo\pyproject.toml")) {
        throw "Sai thư mục: không tìm thấy $repo/pyproject.toml. Hãy mở terminal ở thư mục cha của ba repo."
    }
    git -C "$workspace\$repo" status --short --branch
    if ($LASTEXITCODE -ne 0) { throw "Không kiểm tra được Git của $repo" }
}
foreach ($repo in @("free-codex-cli", "free-claude-cli")) {
    $branch = git -C "$workspace\$repo" branch --show-current
    if ($LASTEXITCODE -ne 0 -or ([string]$branch).Trim() -ne "integration/protoverta") {
        throw "$repo chưa ở nhánh integration/protoverta. Kiểm tra thay đổi chưa commit trước khi tự chuyển nhánh."
    }
}
$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $pythonExe = $pythonCommand.Source
    $pythonArgs = @("-3")
} else {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $pythonExe = $pythonCommand.Source
    $pythonArgs = @()
}
& $pythonExe @pythonArgs -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) { throw "Python được chọn chưa chạy được hoặc thấp hơn 3.11" }
Write-Host "Python dùng để tạo preview: $pythonExe $pythonArgs"
Get-Command codex, claude -ErrorAction Stop | Select-Object Name, Source
~~~

Đọc kết quả Git để biết code nào sẽ được build. Build lấy cả thay đổi local chưa
commit; commit trước sẽ dễ tái hiện hơn. Hướng dẫn không tự chuyển nhánh hay bỏ
thay đổi của bạn. Nếu máy không có launcher py, đoạn lệnh sẽ dùng python trên
PATH; vì vậy không bắt buộc cài thêm py khi Python phù hợp đã có sẵn.

## 2. Build và cài ba wheel vào venv riêng

Phần tool, reasoning và cấu hình MCP riêng từng lần chạy được giải thích trong
[harness-tools.md](harness-tools.md). Không cần MCP để kiểm tra chuyển giao thức.

~~~powershell
$previewBase = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ProtovertaPreviews'
$previewRoot = Join-Path $previewBase ([guid]::NewGuid().ToString('N'))
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (($previewRoot.TrimEnd('\') + '\').StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preview phải nằm ngoài TEMP để Codex tạo được helper. Chọn thư mục preview riêng ngoài TEMP.'
}
New-Item -ItemType Directory -Path $previewRoot -ErrorAction Stop | Out-Null
& $pythonExe @pythonArgs -m venv "$previewRoot\venv"
if ($LASTEXITCODE -ne 0) { throw "Không tạo được venv preview" }
$previewPython = "$previewRoot\venv\Scripts\python.exe"
& $previewPython -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) { throw "Preview cần Python 3.11 trở lên" }
& $previewPython -m pip install build hatchling pytest
if ($LASTEXITCODE -ne 0) { throw "Không cài được công cụ build/test trong preview" }
foreach ($repo in @("protoverta", "free-codex-cli", "free-claude-cli")) {
    & $previewPython -m build --wheel --no-isolation --outdir "$previewRoot\dist" "$workspace\$repo"
    if ($LASTEXITCODE -ne 0) { throw "Build wheel thất bại: $repo" }
}
$previewWheels = @(Get-ChildItem "$previewRoot\dist\*.whl" | Select-Object -ExpandProperty FullName)
if ($previewWheels.Count -ne 3) { throw "Cần có đúng ba wheel tương ứng với ba dự án" }
& $previewPython -m pip install --no-deps @previewWheels
if ($LASTEXITCODE -ne 0) { throw "Không cài được các wheel vào preview" }
& $previewPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency trong preview không tương thích" }
Write-Host "Thư mục preview: $previewRoot"
~~~

Tải công cụ build có thể cần Internet. Ba wheel của dự án lấy từ source local.
Không đặt `CODEX_HOME` dưới TEMP: Codex có thể từ chối tạo PATH alias/helper
ở đó. Thư mục mới trên nằm dưới `LocalApplicationData\ProtovertaPreviews`,
không thay đổi bản cài hay config thật. Preview cũ trong TEMP vẫn giữ nguyên;
hãy tạo preview mới nếu cần kiểm tra helper, thay vì di chuyển session đang mở.
**Luôn gọi executable bằng đường dẫn trong $previewRoot\venv\Scripts**; gõ riêng
codex-ca hoặc claude-ca có thể chạy bản cũ trên PATH. Khi sửa code sau bước này,
cần build/cài lại wheel preview để thử đúng code mới.

## 3. Thử tự động bằng endpoint local, không dùng key thật

Nhóm này dùng CLI thật nhưng phía provider là server giả trên 127.0.0.1.
Mỗi lượt tự tạo config/session tạm và lọc biến môi trường; không cần nhập key
hay gọi model thật, không tốn phí API provider.

~~~powershell
foreach ($client in @("codex", "claude")) {
    foreach ($protocol in @("chat", "messages", "responses")) {
        & $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client $client --upstream $protocol --installed-adapter --tool-loop
        if ($LASTEXITCODE -ne 0) { throw "CLI smoke thất bại: $client / $protocol" }
    }
}
& $previewPython "$workspace\protoverta\scripts\smoke_resume.py" --client codex --source responses --target chat --adapter-source "$workspace\free-codex-cli" --change-provider --change-model --tool-loop --trace
if ($LASTEXITCODE -ne 0) { throw "Thử resume Codex thất bại" }
& $previewPython "$workspace\protoverta\scripts\smoke_resume.py" --client claude --source messages --target responses --adapter-source "$workspace\free-claude-cli" --change-provider --change-model --tool-loop
if ($LASTEXITCODE -ne 0) { throw "Thử resume Claude thất bại" }
~~~

Cần thấy **passed: true** ở sáu lượt CLI và hai lượt resume. Tool chỉ đọc: Codex
dùng get_goal, Claude đọc file mẫu tạm. Resume kiểm tra session ID, câu trả lời
trước và lịch sử tool khi đổi provider ID/model giả. Probe Codex tắt summary từ
phiên đầu; đây không phải phép thử chuyển reasoning state.

Sáu lượt CLI dùng **wheel đã cài**; hai lượt resume có --adapter-source nên dùng
**source launcher đang checkout**. Đừng sửa code giữa lúc build và test nếu
muốn kiểm tra cùng một snapshot. Kết quả local không chứng nhận model thật.

### Thử riêng cài, cập nhật và gỡ

Script dưới đây tự chuyển app data/profile sang thư mục tạm, dùng -NoPathChanges,
bỏ qua cài CLI thật và dùng core wheel local. Nó không cài đè bản đang dùng:

~~~powershell
$coreWheel = Get-ChildItem "$previewRoot\dist\protoverta-*.whl" | Select-Object -ExpandProperty FullName
& $previewPython "$workspace\protoverta\scripts\smoke_windows_install.py" --wheel $coreWheel
if ($LASTEXITCODE -ne 0) { throw "Thử cài/cập nhật/gỡ trong môi trường tạm thất bại" }
~~~

Cần thấy install, update, uninstall, config_preserved đều **true** cho hai
wrapper. Update ở đây là cài lại cùng checkout/phiên bản, chưa phải thử mọi đường
nâng/hạ phiên bản cũ. Script có thể tải dependency build. Không thêm --remote
khi thử thay đổi local chưa phát hành.

## 4. Chuẩn bị config và session riêng để tự thử giao diện

Từ bước này bạn có thể nhập key thật để chủ động thử provider ở bước 5. Trước
tiên chuyển state của **cửa sổ này và các tiến trình con** vào preview; không
thay đổi biến môi trường User/System hay PATH lâu dài:

~~~powershell
foreach ($folder in @("profile", "roaming", "local", "codex", "claude", "work")) {
    New-Item -ItemType Directory -Force -Path "$previewRoot\$folder" -ErrorAction Stop | Out-Null
}
$env:USERPROFILE = "$previewRoot\profile"
$env:HOME = "$previewRoot\profile"
$env:APPDATA = "$previewRoot\roaming"
$env:LOCALAPPDATA = "$previewRoot\local"
$env:CODEX_HOME = "$previewRoot\codex"
$env:CLAUDE_CONFIG_DIR = "$previewRoot\claude"
$env:CODEX_CA_CONFIG = "$previewRoot\codex-providers.toml"
$env:CODEX_CA_ENV_FILE = "$previewRoot\codex.env"
$env:CODEX_CA_CACHE = "$previewRoot\codex-cache.json"
$env:CLAUDE_CA_CONFIG = "$previewRoot\claude-providers.toml"
$env:CLAUDE_CA_ENV_FILE = "$previewRoot\claude.env"
$env:CLAUDE_CA_CACHE = "$previewRoot\claude-cache.json"
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
$env:CLAUDE_CODE_DISABLE_AUTO_MEMORY = "1"
Set-Location "$previewRoot\work"
~~~

Hai biến *_CACHE trỏ tới **file JSON**, không phải thư mục. Không chép nguyên
config/session đang dùng vào preview cho lượt đầu.

Lệnh thủ công vẫn có thể kế thừa biến provider, proxy hoặc telemetry khác từ
terminal: đổi HOME không xóa các biến đó. Nếu đã đặt chúng, hãy chỉ giữ thiết
lập mình muốn dùng. Các probe bước 3 tự lọc môi trường; thao tác thủ công không
có bảo đảm lọc tương tự.

Mở từng trang setup, dừng server thứ nhất trước khi chạy lệnh thứ hai:

~~~powershell
& "$previewRoot\venv\Scripts\codex-ca.exe" setup
# Sau khi đóng hoặc Ctrl+C dừng server setup Codex:
& "$previewRoot\venv\Scripts\claude-ca.exe" setup
~~~

Trong giao diện:

1. Thêm provider bằng API prefix đúng, ví dụ **https://host/v1**, không nhập cả
   /chat/completions, /messages hay /responses vào Base URL.
2. Chọn đúng chuẩn upstream; chọn gateway khi cần chuyển đổi. Giữ Bearer trừ
   khi provider yêu cầu x-api-key.
3. Nhập key trong form preview, không gửi lên chat hay commit file key.
4. Chọn model hoặc nhập ID thủ công. Danh sách /models không chứng minh model
   hỗ trợ streaming/tool. Chỉ chọn chính sách reasoning provider thực sự hỗ trợ.
5. Thử thêm/sửa/xóa provider mẫu, tên hiển thị, tìm model, Refresh, nền sáng/tối
   và lưu default. Lượt đầu chọn **Save as default**, rồi dừng setup để thử CLI
   bằng lệnh có giới hạn bên dưới.

Tên giá trị protocol trong TOML của hai launcher khác nhau; chọn trên giao diện
sẽ tránh phải tự đoán tên. Sau khi dừng setup, kiểm tra config:

~~~powershell
& "$previewRoot\venv\Scripts\codex-ca.exe" config validate
if ($LASTEXITCODE -ne 0) { throw "Config preview Codex chưa hợp lệ" }
& "$previewRoot\venv\Scripts\claude-ca.exe" validate
if ($LASTEXITCODE -ne 0) { throw "Config preview Claude chưa hợp lệ" }
~~~

## 5. Chủ động gọi provider thật bằng vài request ngắn

**Mục này có thể tốn quota/phí API.** Chỉ chạy sau khi đã lưu default trong
preview. Key được lưu ở file preview đã chỉ định, không phải kho mã hóa bí mật.

Trước hết thử câu hỏi không yêu cầu tool:

~~~powershell
& "$previewRoot\venv\Scripts\codex-ca.exe" run -- --no-daemon --sandbox read-only exec --ignore-user-config --ignore-rules --skip-git-repo-check "Reply briefly. Do not use tools."
if ($LASTEXITCODE -ne 0) { throw "Request Codex thất bại; đọc lỗi trước khi thử lại" }
& "$previewRoot\venv\Scripts\claude-ca.exe" --% print -- --setting-sources "" --strict-mcp-config --tools "" --no-session-persistence "Reply briefly. Do not use tools."
if ($LASTEXITCODE -ne 0) { throw "Request Claude thất bại; đọc lỗi trước khi thử lại" }
~~~

**--%** là cú pháp PowerShell trên Windows để giữ nguyên phần đối số phía sau,
đặc biệt các chuỗi rỗng "" trên PowerShell 5.1. Đường dẫn executable vẫn được
mở rộng trước --%. Không dùng biến PowerShell phía sau dấu này vì chúng không
được mở rộng như bình thường.

Codex bỏ qua user config/rules, dùng sandbox read-only và không gắn vào daemon.
Claude không bật tool và không lưu session. Đây chỉ là lượt thử đầu, không đánh
giá tool/resume. Nếu CLI không hiểu cờ lệnh, đọc --help của phiên bản đang cài;
không bỏ các cờ cô lập chỉ để ép test qua.

Nếu tạo phiên Responses gốc rồi định chuyển protocol, tắt summary từ phiên đầu
bằng **-c "model_reasoning_summary='none'"**. Việc này không chuyển reasoning ẩn,
chữ ký hay dữ liệu mã hóa của phiên cũ thành lịch sử có thể chuyển đổi. Xem
[cấu hình Codex chính thức](https://learn.chatgpt.com/docs/config-file/config-reference)
và [giới hạn resume](../compatibility/resume.md).

Tiếp theo thay PROVIDER_ID và MODEL_ID bằng đúng giá trị đã cấu hình:

~~~powershell
& "$previewRoot\venv\Scripts\codex-ca.exe" doctor -p PROVIDER_ID -m MODEL_ID --allow-live --check text --check streaming
if ($LASTEXITCODE -ne 0) { throw "Doctor preview Codex chưa đạt" }
& "$previewRoot\venv\Scripts\claude-ca.exe" doctor -p PROVIDER_ID -m MODEL_ID --allow-live --check text --check streaming
if ($LASTEXITCODE -ne 0) { throw "Doctor preview Claude chưa đạt" }
~~~

Đây là route doctor cho **gateway**, chủ động cho phép gọi thật bằng --allow-live.
Khi text/streaming đã qua, có thể thêm --check streaming_tools --check tool_result,
rồi thử một tool chỉ đọc, resume sau vòng tool đã hoàn tất và nhiều phiên khác
provider/model. Model miễn phí vẫn có rate limit; đọc lỗi trước khi thử lại.

Cuối cùng thử nút **Run** trong setup: server setup phải dừng, CLI phải tiếp tục
chạy trong terminal đến khi bạn thoát. Terminal tương tác với agent thật vẫn
cần kiểm tra thủ công; test bằng CLI giả ở mục 6 không thay thế nó.

## 6. Tùy chọn: coverage và giao diện tự động

Không bắt buộc để thử model thật. Dùng tiếp các biến của cửa sổ preview;
nếu đã đóng thì cần thiết lập lại hoặc tự khôi phục đúng đường dẫn.

### Đo test source và Python CLI con

~~~powershell
& $previewPython -m pip install coverage==7.17.0
if ($LASTEXITCODE -ne 0) { throw "Không cài được công cụ coverage" }
& $previewPython "$workspace\protoverta\scripts\check_coverage.py" --codex-source "$workspace\free-codex-cli" --claude-source "$workspace\free-claude-cli"
if ($LASTEXITCODE -ne 0) { throw "Coverage chưa đạt; xem log ở đường dẫn script báo" }
~~~

Script chạy toàn bộ test source của ba repo, đo dòng/nhánh và kiểm tra Python
CLI con thực sự có dữ liệu qua lượt --help. Kết quả được lưu ở thư mục tạm riêng.
Đây là đo **source**, không phải wheel hay binary Codex/Claude, cũng không bao
gồm mọi tiến trình cháu. Nó không nới danh sách biến cho phép để mang key vào test.

Mỗi bộ test có thể mất vài phút. Nếu báo lỗi, terminal sẽ hiện tên các test
thất bại và đường dẫn pytest.log để xem chi tiết. “Coverage chưa đạt” trong khối
lệnh trên nghĩa là công cụ kiểm tra chưa chạy qua, **không nhất thiết là phần
trăm coverage thấp**; công cụ hiện không đặt ngưỡng phần trăm bắt buộc. Không bỏ
qua test lỗi hay đổi thành pass chỉ để lấy được báo cáo.

### Trình duyệt → launcher/gateway thật → CLI giả

~~~powershell
& $previewPython -m pip install playwright==1.64.1
if ($LASTEXITCODE -ne 0) { throw "Không cài được công cụ test trình duyệt" }
$env:PLAYWRIGHT_BROWSERS_PATH = "$previewRoot\browsers"
& $previewPython -m playwright install chromium --only-shell
if ($LASTEXITCODE -ne 0) { throw "Không tải được Chromium dùng cho test" }
& $previewPython "$workspace\protoverta\scripts\browser_setup_smoke.py" --source "$workspace\free-codex-cli" --package codex_adapter --installed --launch-fixture
if ($LASTEXITCODE -ne 0) { throw "Test giao diện/foreground Codex thất bại" }
& $previewPython "$workspace\protoverta\scripts\browser_setup_smoke.py" --source "$workspace\free-claude-cli" --package claudapter --installed --launch-fixture
if ($LASTEXITCODE -ne 0) { throw "Test giao diện/foreground Claude thất bại" }
~~~

Browser/app dùng profile, config và key giả riêng. Nút Run gọi launcher/gateway
thật rồi chạy tiến trình Python giả thay agent. Test kiểm tra model, key upstream
không lọt vào child, HTTP local thành công, listener setup đã đóng nhưng child
vẫn sống và mã thoát truyền về đúng. Không gọi provider thật hay mô phỏng đủ PTY.

Các nhóm SDK, property, kiểu dữ liệu, tải/hủy kết nối và schema có mục đích khác
nhau; không gộp thành tuyên bố “đã hỗ trợ mọi thứ”. Xem
[ma trận kiểm thử](validation-matrix.md), [conformance](conformance.md),
[kết quả kiểm thử](validation-2026-10-26.md) và
[đợt bổ sung coverage](coverage-expansion-2026-10-26.md).
Full schema OpenResponses và một số usage/reasoning state vẫn có giới hạn.

## 7. Kết thúc preview và quay lại bản đang dùng

Thoát CLI, dừng server preview rồi **đóng cửa sổ PowerShell này**. Mở terminal
mới và chạy codex-ca/claude-ca như trước: bản cài trên PATH vẫn là bản cũ.
**Không cần chạy installer GitHub để rollback** vì chưa cài đè bản thật.

Không chạy installer thông thường từ cửa sổ đã đổi LOCALAPPDATA/USERPROFILE:
nó có thể cài vào vùng preview thay vì nơi cài thật. Muốn thao tác bản cài thật,
hãy mở cửa sổ mới ngoài thư mục repo và kiểm tra môi trường trước.

Có thể giữ $previewRoot để xem log/thử lại. Muốn xóa, ghi lại đường dẫn trước
khi đóng terminal rồi chỉ xóa đúng thư mục preview đó sau khi các tiến trình
đã dừng. Trong đó có thể chứa key preview; đừng gửi nguyên thư mục kèm báo lỗi
hoặc đưa lên Git.
