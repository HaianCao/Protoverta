# P10 — Rich Tool Results (2026-11-04)

```text
ID: P10
Status: COMPLETE
Baseline commits / CLI / SDK: protoverta 22b020d, free-codex-cli e757fd3, free-claude-cli 1d727fa; Codex CLI 0.166.0, Claude Code 2.1.308
Observed issue or scope limit:
1. Prior to P10, Chat `role: "tool"` messages only accepted string `content` and rejected structured text-part arrays (`[{"type": "text", "text": "..."}]`), while multi-block text arrays in Messages/Responses `tool_result` / `function_call_output` were flattened into a single string when no image was present, losing block boundaries on Messages <-> Responses and Chat -> Messages/Responses.
2. Messages `tool_result` with `document` (PDF) raised a generic "tool results support text/image content only" message instead of the explicit "PDF tool results have no portable equivalent" contract used by Responses.
3. A complete 6-direction matrix for `text`, `error`, `structured content`, `image`, `PDF`, and `resource_link` tool results with explicit preserve / policy-specific / reject rationale was needed.
Reproduction:
- pytest tests/test_p10_rich_tool_results.py tests/test_core.py tests/test_profiles.py
Contract decision:
1. 6-Direction Rich Tool Result Matrix:
   - Text string (`"content": "..."` / `"output": "..."`): PRESERVE across all 6 directions (`Chat <-> Messages`, `Chat <-> Responses`, `Messages <-> Responses`).
   - Structured text-part arrays (`[{"type": "text"|"input_text", "text": "..."}]`): PRESERVE across all 6 directions. Multi-part text block boundaries are preserved on `Messages <-> Responses` and `Chat -> Messages/Responses`, and joined losslessly into string `content` when targeting `Chat` (for SDK/OpenAI compatibility) without changing role.
   - Error flag (`is_error=true` on Messages `tool_result`): POLICY-SPECIFIC on `Messages -> Chat` and `Messages -> Responses` (rejected in `strict` mode because Chat/Responses lack a separate boolean error field on tool outputs; preserved via explicit `[Tool execution error]\n` prefix under `client_profile="claude"`). `is_error=false` is accepted in strict mode.
   - Image in tool results (`image` / `input_image`): PRESERVE on `Messages <-> Responses`; REJECT on `Chat <-> Messages` and `Chat <-> Responses` because Chat `role: "tool"` does not support portable image blocks and the gateway never relocates tool images into a synthetic `role: "user"` turn.
   - PDF in tool results (`file` / `document` / `input_file`): REJECT across all 6 directions (`PDF tool results have no portable equivalent`); never silently dropped or moved to a user turn.
   - Resource link in tool results (`resource_link` / MCP resource URIs): REJECT across all 6 directions; the gateway never fetches, dereferences, or strips resource links.
2. Ordering & History Replay:
   - Mixed user text + tool results, multiple tool results, and reordered parallel tool results (`[c2, c1]`) preserve exact call IDs, roles, and relative ordering across all 6 directions.
   - Text after tool call is consistently rejected when targeting Chat or Responses across request history, completed JSON responses, and SSE streams (with `prepare_codex_request` stripping only validated whitespace-only assistant placeholders on `Responses -> Chat`).
   - Gateway body, stream, and tool argument size limits remain enforced before any upstream I/O.
Files changed:
- protoverta/src/protoverta/core.py
- protoverta/tests/test_p10_rich_tool_results.py
- protoverta/docs/compatibility/counting-caching-media.md
- protoverta/docs/development/p10-rich-tool-results-2026-11-04.md
- ADAPTER_IMPLEMENTATION_PLAN_2026-11-03.md
Tests run and actual results:
- protoverta: pytest tests/test_p10_rich_tool_results.py tests/test_core.py tests/test_profiles.py -> 73 passed
- protoverta: scripts/check_types.py -> PASS
- protoverta: ruff check --no-cache -> All checks passed
Acceptance evidence:
- All 6 directions tested for text, structured text-part arrays, error (`strict` vs `claude`), image (`Messages <-> Responses` preserved, Chat rejected), PDF (rejected), resource_link (rejected), mixed user text + reordered parallel tool results, text-after-tool-call consistency (request, JSON response, SSE), and body size limits.
Remaining limits / pending gates:
- Arbitrary JSON objects as raw `content`/`output` (without text/image content blocks), PDFs in tool results, and `resource_link` blocks remain intentionally rejected across portable routes.
User review: Autonomous execution per user instruction
Commit(s): Pending P10 commit
```

## Six-Direction Tool Result Support Matrix

| Tool Result Content Kind | Chat → Messages | Chat → Responses | Messages → Chat | Messages → Responses | Responses → Chat | Responses → Messages | Rationale |
|---|---|---|---|---|---|---|---|
| **Plain text** (`str`) | `preserve` | `preserve` | `preserve` | `preserve` | `preserve` | `preserve` | Direct 1:1 string mapping across `role: "tool"`, `tool_result`, and `function_call_output`. |
| **Structured text array** (`[{"type": "text"\|"input_text", "text": ...}]`) | `preserve` | `preserve` | `preserve` (joined string) | `preserve` (array blocks) | `preserve` (joined string) | `preserve` (array blocks) | Block array structure is preserved between Messages and Responses; Chat `role: "tool"` receives concatenated string content for strict Chat schema compatibility while accepting text-part arrays on input. |
| **Error flag** (`is_error: true`) | `n/a` (Chat has no flag) | `n/a` (Chat has no flag) | `policy-specific` (`claude` profile prefixes `[Tool execution error]\n`; `strict` rejects) | `policy-specific` (`claude` profile prefixes `[Tool execution error]\n`; `strict` rejects) | `n/a` (Responses has no flag) | `n/a` (Responses has no flag) | Chat and Responses lack a boolean error field on tool outputs; `strict` refuses silent attribute loss while `client_profile="claude"` applies an explicit, deterministic text prefix. |
| **Image** (`image` / `input_image`) | `reject` | `reject` | `reject` | `preserve` | `reject` | `preserve` | Messages `tool_result.content` and Responses `function_call_output.output` both support inline/URL image blocks; Chat `role: "tool"` does not, and relocating images to `role: "user"` would falsify turn attribution. |
| **PDF document** (`file` / `document` / `input_file`) | `reject` | `reject` | `reject` | `reject` | `reject` | `reject` | PDF blocks are only portable in ordinary user turns; tool-result PDFs have no verified cross-protocol contract and are never dropped or moved to user turns. |
| **Resource link** (`resource_link`) | `reject` | `reject` | `reject` | `reject` | `reject` | `reject` | Neither Chat, Messages, nor Responses has a portable cross-provider resource-link block, and the gateway never dereferences external URIs. |
