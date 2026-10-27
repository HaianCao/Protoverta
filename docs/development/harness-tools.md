# Tool, reasoning và MCP trong preview

Protoverta chuyển giao thức. Tool do harness cung cấp và thực thi; gateway
không tự tạo trình duyệt, crawler, shell hoặc quyền truy cập mạng.

## Codex: helper khác với tool được gửi cho model

Đặt preview ngoài TEMP theo [local-preview.md](local-preview.md). Trong một
preview mới ngoài TEMP, kiểm tra `codex debug models --bundled` không còn cảnh
báo `Refusing to create helper binaries`. Điều này chưa chứng minh model được
quảng bá `apply_patch`: danh sách tool còn phụ thuộc metadata và phiên bản CLI.

Không tìm `apply_patch` bằng cách quét toàn bộ ổ C:. Nếu harness không quảng bá
tool đó, model có thể dùng shell ghi file trong phạm vi quyền đã cấp. Lỗi shell
`apply_patch: command not found` khác với lỗi adapter chuyển đổi tool call.

Nếu có catalog Codex hợp lệ, đúng phiên bản CLI và đúng model, có thể chọn riêng
cho terminal preview:

```powershell
$env:CODEX_CA_MODEL_CATALOG = 'C:\preview-config\models.json'
```

Wrapper kiểm tra file tối đa 2 MiB, slug duy nhất và model đang chọn phải có
entry khớp tuyệt đối. Codex tiếp tục kiểm tra schema của catalog. Wrapper không
tự copy metadata GPT sang model khác, không đoán context window hay khả năng
tool từ tên model. Bỏ biến này để dùng hành vi mặc định của Codex.

Tham khảo [catalog Codex](https://learn.chatgpt.com/docs/model-catalogs).

## Reasoning: chọn đúng route

**Không cần tự chọn khi thêm provider mới:** để mặc định Automatic. Các config
không khai báo `upstream_reasoning` cũng dùng tự động. Chính sách thủ công đã
ghi rõ trong config cũ vẫn được giữ; có thể chuyển sang Automatic trong mục
Advanced: reasoning settings một lần rồi lưu.

Launcher tự xử lý theo cặp giao thức và endpoint, không gửi request thăm dò,
không tốn thêm quota, không đoán khả năng từ tên model:

| Route | Chính sách |
| --- | --- |
| Chat ↔ Responses | Tự bật ánh xạ unsigned public text tương đương; giữ reasoning riêng với câu trả lời |
| Route chéo có Messages | Tự yêu cầu tắt upstream reasoning; dùng `reasoning.enabled=false` tại đúng endpoint OpenRouter Chat/Responses, chuẩn thông thường ở endpoint khác |
| Native cùng giao thức | Bảo toàn payload trong phạm vi kiểm tra lifecycle; không bảo đảm state dùng lại được ở provider khác |

`upstream_reasoning` là yêu cầu gửi upstream, không phải bộ lọc response.
`openrouter_disabled` chỉ dành cho OpenRouter. Model có reasoning bắt buộc có
thể từ chối hoặc bỏ qua yêu cầu tắt; khi đó route không tương đương vẫn báo lỗi.
Không dùng reasoning summary, chữ ký giả hoặc text có nhãn để vượt giới hạn này.

Các lựa chọn kỹ thuật nằm trong Advanced: reasoning settings. Ô effort tự khóa
và hiện giải thích khi route đang yêu cầu tắt upstream reasoning. Auto chọn
chính sách chuyển đổi, **không chứng nhận model có thể tắt reasoning**: nếu model
bắt buộc trả reasoning không tương đương thì vẫn báo lỗi, không tự đổi model,
đổi endpoint hay chạy lại một turn có tool calls.
Danh sách `/models` không phải bằng chứng model hỗ trợ reasoning/tool/effort.

## Gắn MCP server search/fetch riêng từng phiên

Cả hai wrapper nhận cùng một file JSON **dành cho wrapper**. Đây là schema
nhỏ, chỉ hỗ trợ stdio: command tuyệt đối trỏ tới executable đã cài, args và tên
biến môi trường. Không tự tải/chạy `npx`, không cài MCP, không đăng ký toàn cục.

Nếu thiếu biến MCP cần thiết trong môi trường tiến trình con, Run sẽ báo lỗi
ngay trên trang setup, giữ trang mở và không lưu mặc định mới. Tên biến tuân theo
hệ điều hành: không phân biệt chữ hoa/thường trên Windows, phân biệt trên Unix.

```json
{
  "mcpServers": {
    "search": {
      "command": "C:/path/to/installed/mcp-server.exe",
      "args": [],
      "env_vars": ["SEARCH_TOKEN"]
    }
  }
}
```

Thay executable bằng server bạn đã chọn và cài; server đó phải thực sự cung cấp
search/fetch. Không đặt key trong args hoặc JSON. Biến `SEARCH_TOKEN` phải có
trong môi trường tiến trình con; wrapper không lấy ngầm key provider để đưa cho
MCP server khác. Bỏ `env_vars` nếu server không cần credential.

```powershell
$env:CODEX_CA_MCP_CONFIG = 'C:\preview-config\mcp.json'
$env:CLAUDE_CA_MCP_CONFIG = 'C:\preview-config\mcp.json'
# Sau đó chạy executable preview bằng đường dẫn đầy đủ như local-preview.md.
```

Codex nhận `mcp_servers` qua đối số config trong lần chạy, chờ server khởi động
với `required=true`; không sửa config.toml. Claude nhận snapshot JSON riêng qua
`--strict-mcp-config --mcp-config`, xóa snapshot sau khi child kết thúc, kể cả
child báo lỗi. Snapshot chỉ chứa tham chiếu `${TEN_BIEN}`; Claude mở rộng biến
từ môi trường tiến trình, không ghi giá trị credential vào snapshot. Nếu OS
buộc dừng wrapper trước khi cleanup, file còn lại cũng không chứa key. Đây là cấu
hình riêng cho lần chạy; Claude không nạp thêm server MCP đã đăng ký toàn cục.

Tắt `ENABLE_TOOL_SEARCH` ở Claude chỉ tắt khám phá tool động, không cấm MCP
được nạp sẵn và không đồng nghĩa tắt Internet. WebSearch tích hợp của provider
khác với MCP search/fetch. MCP server còn phải truy cập được mạng trong chính
môi trường của nó; adapter không mở quyền sandbox hoặc sửa proxy để ép chạy.
Tham khảo [MCP Claude Code](https://code.claude.com/docs/en/mcp).

## Test không dùng mạng hoặc key thật

Sau khi build preview mới, từ thư mục workspace, dùng `$previewPython`:

```powershell
& $previewPython "$workspace\protoverta\scripts\smoke_tool_search.py" --upstream chat --installed-adapter
& $previewPython "$workspace\protoverta\scripts\smoke_tool_search.py" --upstream messages --installed-adapter
& $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client claude --upstream chat --tool-loop --mcp-fixture --installed-adapter
& $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client claude --upstream responses --tool-loop --mcp-fixture --installed-adapter
```

Kiểm tra exit code sau **mỗi** lệnh; chỉ pass khi có tool result thực sự được
harness trả về model. Fixture MCP chỉ trả chuỗi tổng hợp, không tìm Internet.
Catalog mang slug fixture trong bài test chỉ áp dụng model giả của test, không
phải catalog cho model/provider thật. Các test tự động này dùng state TEMP và
có thể còn cảnh báo helper; chúng không kiểm tra shell `apply_patch`.

Để kiểm tra Auto reasoning bằng CLI thật và provider local giả lập, thêm
`--upstream-reasoning auto` vào `scripts/smoke_clients.py`, ví dụ:

```powershell
& $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client codex --upstream chat --upstream-reasoning auto --tool-loop --installed-adapter
& $previewPython "$workspace\protoverta\scripts\smoke_clients.py" --client claude --upstream chat --upstream-reasoning auto --tool-loop --installed-adapter
```

Các case này ghi chính sách Auto vào config wrapper tạm và kiểm tra tool result
được trả lại model. Không gọi provider thật hay thay config đang dùng.
