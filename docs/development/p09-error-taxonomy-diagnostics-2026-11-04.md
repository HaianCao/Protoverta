# P09 — Error Taxonomy and Diagnostics (2026-11-04)

```text
ID: P09
Status: COMPLETE
Baseline commits / CLI / SDK: protoverta 810231b, free-codex-cli 529239d, free-claude-cli e65429a; Codex CLI 0.166.0, Claude Code 2.1.308; openai 2.30.0, anthropic 0.92.0
Observed issue or scope limit:
1. Prior to P09, GatewayFailure and RequestObservation did not carry structured phase/feature/path metadata or agent/chain correlation digests, and policy refusals (refusal, content_filter) shared the generic unsupported_feature code.
2. Messages pause_turn was rejected as a generic ConversionError/StreamError rather than a structured UnsupportedFeatureError("pause_turn") / UnsupportedStreamFeatureError("pause_turn").
3. Completed responses or streams truncated at max_output_tokens / length / max_tokens recorded outcome="success" without distinguishing truncated_output in diagnostics or response headers.
4. Web UI diagnostics lines showed raw fields without actionable remediation or warnings against switching routes mid-session when opaque provider state is present.
Reproduction:
- pytest tests/test_p09_error_taxonomy_diagnostics.py tests/test_failures.py tests/test_observability.py tests/test_session_diagnostics.py
- python scripts/check_types.py && python scripts/check_sdk.py
Contract decision:
1. Structured error taxonomy: GatewayFailure and wire error envelopes now include optional validated phase ("request", "preflight", "admission", "upstream", "response", "stream"), feature ("refusal", "content_filter", "stop_sequence", "pause_turn", "stream_usage_policy", "codex_closed_resume", "compaction", "reasoning"), and ASCII path (also populating param on Responses SSE error events).
2. Policy refusal vs network/unsupported: feature in {"refusal", "content_filter"} maps to code="policy_refusal" and RequestObservation.outcome="policy_refusal" with HTTP 422 and x-should-retry: false before headers (or SSE error event after partial output), preventing clients from treating refusals as retryable network failures.
3. Pause turn: Messages stop_reason="pause_turn" raises UnsupportedFeatureError("pause_turn") in JSON and UnsupportedStreamFeatureError("pause_turn") in SSE; it is never converted into normal end_turn completion.
4. Truncated output: JSON and SSE completions that finish with length / max_tokens / max_output_tokens record outcome="truncated_output" (with X-Protoverta-Finish-Category: truncated_output on JSON responses) while still reconciling token quotas accurately.
5. Bounded correlation & redaction: Authenticated requests may supply X-Protoverta-Agent-ID and X-Protoverta-Chain-ID (1..128 printable ASCII chars), recorded as session-salted 64-hex HMAC-SHA256 digests (agent_digest, chain_digest). Unauthenticated, forged, or oversized headers are ignored; correlation headers are never used for auth/routing and never forwarded upstream. All provider error messages redact configured secrets before bounding to 2000 chars.
6. Journal versioning: diagnostic_journal writes schema_version 2 while latest_diagnostics accepts both schema_version 1 and 2, defaulting new optional fields (phase, feature, agent_digest, chain_digest) to None for legacy v1 journals.
7. Web UI diagnostics: Both codex_adapter and claudapter web templates render phase/feature metadata and actionable guidance per outcome, explicitly warning never to switch routes mid-session when opaque provider state (reasoning, pause_turn, compaction) is active.
Files changed:
- protoverta/src/protoverta/errors.py
- protoverta/src/protoverta/failures.py
- protoverta/src/protoverta/observability.py
- protoverta/src/protoverta/core.py
- protoverta/src/protoverta/streaming.py
- protoverta/src/protoverta/stream_pipe.py
- protoverta/src/protoverta/server.py
- protoverta/src/protoverta/session_diagnostics.py
- protoverta/tests/test_observability.py
- protoverta/tests/test_p09_error_taxonomy_diagnostics.py
- protoverta/docs/development/p09-error-taxonomy-diagnostics-2026-11-04.md
- free-codex-cli/src/codex_adapter/web_template.html
- free-claude-cli/src/claudapter/web_template.html
- ADAPTER_IMPLEMENTATION_PLAN_2026-11-03.md
Tests run and actual results:
- protoverta: pytest tests/test_p09_error_taxonomy_diagnostics.py tests/test_failures.py tests/test_observability.py tests/test_session_diagnostics.py -> 90 passed
- protoverta: scripts/check_types.py -> PASS (6 source files + valid/invalid boundary fixtures)
- protoverta: scripts/check_sdk.py -> 29 passed
- free-codex-cli: pytest tests/test_web.py tests/test_gateway_product.py -> 12 passed
- free-claude-cli: pytest tests/test_web.py tests/test_gateway_product.py -> 11 passed
- Ruff across all 3 repositories -> 0 errors
Acceptance evidence:
- Pre-header 422 vs post-header SSE error event verified for policy_refusal and unsupported_reasoning.
- Secret redaction, local validation zero-upstream-I/O, authenticated HMAC correlation digests, and v1/v2 journal reader compatibility verified in test_p09_error_taxonomy_diagnostics.py.
Remaining limits / pending gates:
- Cross-protocol pause_turn continuation and cross-protocol refusal translation remain intentionally rejected under strict semantics.
User review: Autonomous execution per user instruction
Commit(s): Pending P09 commit
```
