# P08 — Context, compaction and resume

Date: 2026-11-04. **P08 COMPLETE within the declared versioned scope.** The user
requested completion of all remaining plan milestones (`P08` through `P14`) with
proactive commits after each `P`. P07 was committed prior to P08 as core
`6891699`, Codex `f37c2fc`, and Claude `f68d661`. No push, merge, or release.

## Section 6 status block

```text
ID: P08
Status: COMPLETE
Baseline commits / CLI / SDK: protoverta 6891699 (main), free-codex-cli f37c2fc (integration/protoverta), free-claude-cli f68d661 (integration/protoverta); Codex CLI 0.166.0, Claude Code 2.1.308; OpenAI SDK 3.37.0, Anthropic SDK 2.3.0
Observed issue or scope limit:
  1. Multi-turn harness compaction: Codex 0.166.0 triggers a text summarization turn when input_tokens exceeds model_auto_compact_token_limit; Claude Code 2.1.308 triggers reactive compaction in a single user turn containing [tool_result, summarization_prompt_text] when input_tokens exceeds the effective autocompact threshold (100k minimum window, 1% override). Previously, _encode_chat rejected a user turn containing both tool_result and user text ("mixed user text and tool results cannot be preserved in Chat"), preventing Claude reactive compaction over Chat upstreams.
  2. Opaque provider-owned compaction vs harness-driven text summarization: Responses compaction items/context_management and Messages compaction blocks/context_management were previously rejected under the generic "reasoning" feature label rather than an explicit "compaction" feature label, and native /v1/responses/compact lacked an explicit UnsupportedFeatureError("compaction") guard if called with a non-Responses upstream config.
  3. Preflight, count endpoints, and UI status lacked a unified count_category ("exact", "estimate", "unavailable") and explicit context_window_tokens display alongside route evidence.
Reproduction:
  - & $p00Python -B protoverta/scripts/smoke_compaction_resume.py --client {codex|claude} --upstream {chat|messages|responses} --adapter-source ../free-{codex|claude}-cli
  - & $p00Python -B protoverta/scripts/smoke_resume.py --client codex --source responses --target chat --adapter-source free-codex-cli --change-model --tool-loop
  - & $p00Python -B protoverta/scripts/smoke_resume.py --client claude --source chat --target responses --adapter-source free-claude-cli --change-provider --change-model --tool-loop
  - & $p00Python -B protoverta/scripts/smoke_resume.py --client codex --source chat --target messages --adapter-source free-codex-cli --change-model
  - & $p00Python -m pytest protoverta/tests/test_p08_context_compaction_resume.py -q
Contract decision:
  - Preserve exact order of tool_result and user text/image/document parts in _encode_chat (emitting {"role": "tool"} and {"role": "user"} messages in part order), matching _encode_responses without stripping or reordering content.
  - Reject opaque compaction items, blocks, and context_management controls across non-native routes with explicit UnsupportedFeatureError("compaction") / UnsupportedStreamFeatureError("compaction") and actionable remediation; keep native POST /v1/responses/compact strictly on native Responses upstreams without rewriting owner/resource fields.
  - Expose X-Protoverta-Context-Count-Category ("exact" | "estimate" | "unavailable") on preflight headers, X-Protoverta-Count-Category ("exact" | "estimate") on count endpoints, and count_category + context_window_tokens in selection_status / launcher model_status.
Files changed:
  - protoverta/src/protoverta/core.py
  - protoverta/src/protoverta/errors.py
  - protoverta/src/protoverta/streaming.py
  - protoverta/src/protoverta/server.py
  - protoverta/src/protoverta/context.py
  - protoverta/src/protoverta/selection_status.py
  - protoverta/scripts/smoke_compaction_resume.py
  - protoverta/tests/test_p08_context_compaction_resume.py
  - protoverta/docs/compatibility/resume.md
  - protoverta/docs/compatibility/context-preflight.md
  - protoverta/docs/compatibility/counting-caching-media.md
  - protoverta/docs/development/p08-context-compaction-resume-2026-11-04.md
  - free-codex-cli/src/codex_adapter/model_selection.py
  - free-codex-cli/tests/test_model_selection.py
  - free-claude-cli/src/claudapter/model_selection.py
  - free-claude-cli/tests/test_model_selection.py
Tests run and actual results:
  - protoverta/tests/test_p08_context_compaction_resume.py + test_core.py + test_profiles.py + test_selection_status.py + test_context_preflight.py + test_token_counting.py + test_product_extensions.py + test_resume_contract.py: all passed
  - free-codex-cli/tests/test_model_selection.py: 1 passed
  - free-claude-cli/tests/test_model_selection.py: 2 passed
  - Actual CLI multi-turn compaction with tool loop (smoke_compaction_resume.py): 6/6 routes passed (codex -> chat/messages/responses; claude -> chat/messages/responses)
  - Actual CLI session resume (smoke_resume.py): codex responses -> chat with model change & tool loop passed; claude chat -> responses with provider/model change & tool loop passed; codex chat -> messages with model change rejected as expected unsupported_history
Acceptance evidence:
  - Reports in P00 preview reports/: p08-compact-{codex,claude}-{chat,messages,responses}.json (6), p08-resume-codex-responses-chat.json, p08-resume-claude-chat-responses.json, p08-resume-codex-chat-messages-rejected.json
Remaining limits / pending gates:
  - Cross-provider opaque reasoning/compaction state remains non-portable by design; closed Codex child resume with custom auxiliary model remains blocked before execution; P09–P14 remain to be executed next.
User review: Autonomous execution requested by user across P08–P14 with per-milestone commits.
Commit(s): Pending P08 commit immediately after Ruff/mypy/git diff checks.
```

## Acceptance map

| Plan requirement | Implementation and evidence | Result |
|---|---|---|
| Multi-turn session with tools reaching actual harness compact threshold | `scripts/smoke_compaction_resume.py` drives real Codex 0.166.0 (`get_goal`, `model_auto_compact_token_limit=1000`) and Claude Code 2.1.308 (`Read` on 2 files, `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=1`) across `chat`, `messages`, and `responses` (6/6 pass) | PASS |
| Distinguish `exact`, `estimate`, `unavailable` in preflight and UI | `X-Protoverta-Context-Count-Category` in `ContextAssessment.headers()`, `X-Protoverta-Count-Category` on count endpoints, and `count_category` + `context_window_tokens` in `selection_status` and both launcher `model_status` helpers | PASS |
| Native `/responses/compact` preserves owner/resource; test compaction in Responses stream | `POST /v1/responses/compact` forwards only to native Responses upstream, preserving provider `id`, `object: "response.compaction"`, and encrypted compaction items verbatim; `StreamValidator` passes native compaction output items while `StreamDecoder._responses` and `_messages` reject cross-protocol stream compaction with `UnsupportedStreamFeatureError("compaction")` | PASS |
| Cross-protocol opaque compaction rejected with clear remediation | `_message_parts`, `_decode_messages`, `_decode_responses`, and `_decode_result` raise `UnsupportedFeatureError("compaction")` with actionable remediation (`retain a compatible native provider/session or compact into plain text before switching protocols`) | PASS |
| Harness performs text summarization; gateway never summarizes on its behalf | Verified in all 6 CLI compaction traces: the harness itself sends the summarization prompt turn and subsequent compacted summary continuation turn; the gateway performs zero background or implicit summarization requests | PASS |
| Resume text/tool history, model switch, signed/encrypted state, and child resume | `smoke_resume.py` and `test_p08_context_compaction_resume.py` verify portable text/tool history across protocols and model changes, Codex `model_switch` mid-conversation developer instruction boundary on Messages, rejection of signed thinking / `redacted_thinking` / encrypted reasoning / `previous_response_id`, and `codex_model_safe_resume` blocking closed child reopen (`multi_agent_v1.resume_agent`) | PASS |
| Large tool output, output reserve, count endpoint errors, and capability window smaller than catalog | Tested in `test_p08_context_compaction_resume.py`: 150 KB tool output is never truncated/rewritten, missing/nonpositive output reserve fails preflight, >64 KiB or HTTP 500 native count responses raise 502 `NativeCountError` without fallback to estimates, and capability `context_window_tokens=2000` overrides larger catalog display names | PASS |

## Observed harness compaction behavior by route

All six real CLI routes were executed in disposable temporary directories with loopback SSE fixtures and source launcher wrappers:

1. **Codex 0.166.0 (`codex -> chat`, `codex -> messages`, `codex -> responses`):**
   - Configured with `model_context_window=2000` and `model_auto_compact_token_limit=1000`.
   - **Turn 1:** Calls `get_goal`; fixture reports `input_tokens=5000`, `output_tokens=50`, exceeding `1000`.
   - **Turn 2:** Codex sends a summarization turn containing its native `CONTEXT CHECKPOINT COMPACTION` prompt. The fixture returns a plain-text summary containing a random `SUMMARY_NONCE_<hex>`.
   - **Turn 3:** Codex starts the post-compaction request with its native handoff prefix (`Another language model started to solve this problem and produced a summary of its thinking process...`) embedding `SUMMARY_NONCE_<hex>`, and receives the final answer marker.
2. **Claude Code 2.1.308 (`claude -> messages`, `claude -> responses`, `claude -> chat`):**
   - Configured with `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` (Claude Code's enforced minimum `rA = 100000`, giving an effective window of `80000` after subtracting `20000` output reserve) and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=1` (`800` token autocompact threshold).
   - **Turn 1:** Calls `Read` on `a.txt` (`input_tokens=600`).
   - **Turn 2:** Calls `Read` on `b.txt` (`input_tokens=20000` on `message_delta`), exceeding both the `800` autocompact threshold and the `15000` reactive compaction minimum.
   - **Turn 3:** Claude Code sends its reactive compaction request (`CRITICAL: Respond with TEXT ONLY. Do NOT call any tools...`) appended as a text block on the user turn holding the second `tool_result`. Because `_encode_chat` now flushes ordered `tool_result` and user text parts into sequential `{"role": "tool"}` and `{"role": "user"}` Chat messages (matching `_encode_responses`), all three protocols preserve this turn losslessly. The fixture returns `<analysis>...</analysis>\n<summary>...SUMMARY_NONCE_<hex>...</summary>`.
   - **Turn 4:** Claude Code strips `<analysis>` and formats `<summary>` into its native continuation message (`This session is being continued from a previous conversation that ran out of context... Continue the conversation from where it left off without asking the user any further questions.`) while retaining the recent tail tool exchange, and receives the final answer marker.

