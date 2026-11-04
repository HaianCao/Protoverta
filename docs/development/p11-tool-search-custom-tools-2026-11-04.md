# P11 — Tool Search and Custom Tools (2026-11-04)

## Scope

Milestone `P11` verifies and hardens the boundary between client-executed tool search, namespaced tools, custom/grammar tools, and provider-hosted tools across `protoverta`, `Codex 0.166.0`, and `Claude Code 2.1.308`.

## Key Changes & Verification

1. **Client vs Hosted Tool Feature Classification (`src/protoverta/capabilities.py`)**:
   - Updated `request_features()` on `Protocol.RESPONSES` so `{"type": "tool_search", "execution": "client"}` is recognized as a client-executed tool rather than `hosted_tools`.
   - Server-executed `{"type": "tool_search", "execution": "server"}` and hosted tools such as `web_search_preview` remain classified as `hosted_tools` and fail closed when translated across protocols.
2. **Real CLI Schema & Tool-Search Verification (`Codex 0.166.0` & `Claude 2.1.308`)**:
   - Executed `scripts/smoke_tool_search.py` across `--upstream responses`, `--upstream chat --adapter-source ../free-codex-cli`, and `--upstream messages --adapter-source ../free-codex-cli` on `codex-cli 0.166.0`. All three 3-round tool-search → MCP `fixture_lookup` loops passed (`returncode == 0`, `PROTOVERTA_SMOKE_OK`).
   - Executed `scripts/smoke_subagents.py --inspect-only` for `codex` (`codex-cli 0.166.0`) and `claude` (`2.1.308 (Claude Code)`), confirming `multi_agent_v1` namespaced tools (`spawn_agent`, `wait_agent`, `send_input`, `resume_agent`, `close_agent`) in Codex and `Agent`, `DeferredToolPlaceholder`, `Read` in Claude with `ENABLE_TOOL_SEARCH=false`.
3. **Combined Namespace, Aliasing, and Streaming Invariants (`tests/test_p11_tool_search_custom_tools.py`)**:
   - Verified simultaneous `ClientToolSearchMap` + `NamespaceMap` + `ToolNameMap` preparation and restoration, partial deferred namespace loading, mid-turn schema conflict rejection, incomplete search status rejection, Claude `tool_reference` fail-closed rejection, and streamed `pv_client_tool_search` buffering until a complete JSON object is validated.
