# P13 — Schema Drift and CI (2026-11-04)

## Scope

Milestone `P13` re-runs the pinned official OpenResponses schema audit without altering the validator or faking results, classifies missing fields by provenance, verifies request-declared control restoration without fabricating defaults, separates pinned SDK/CLI baselines from candidate evaluation, and verifies route-based feature gating and keyless CI invariants.

## Key Changes & Verification

Reconciliation note (2026-11-04): implemented in commit `52b201c`. The
candidate SDK flag is an evaluation mechanism, not evidence that newer SDKs
were tested. The workflow currently defines pinned SDK jobs; no separate
newer-SDK candidate run is certified here. Keep that acceptance gate pending.

1. **Pinned Official OpenResponses Re-audit & Provenance Classification (`scripts/check_openresponses.py`, `docs/development/openresponses-audit.md`)**:
   - Re-ran `scripts/check_openresponses.py` against `openresponses@8907bc3b7c4300ff5c21fd647cfde9929243afa1` (`runner_sha256: c825257dc3b45165e31e3b02c8e52b2188e91f1de0e7af72f613cfa4a1e80e4c`). Verdict remains honest: `FAILED_FULL_SCHEMA` (`0 passed, 12 failed, 0 skipped`, exit code `1`).
   - Added `classify_missing_field()` and `summary.provenance_classes` grouping findings into `gateway-owned` (`truncation`: 10), `request-declared` (`parallel_tool_calls`: 12, `temperature`: 12, `text`: 10, `tool_choice`: 12, `tools`: 10, `top_p`: 12), `upstream-measured` (`service_tier`: 12, `usage`: 6), and `unavailable` (`frequency_penalty`: 12, `presence_penalty`: 12, `top_logprobs`: 12).
2. **Equivalent Controls Without Zero/Default Fabrication (`src/protoverta/response_context.py`, `tests/test_p13_schema_drift_ci.py`)**:
   - Verified that `restore_request_context` projects explicitly declared portable controls (`instructions`, `max_output_tokens`, `temperature`, `top_p`, `parallel_tool_calls`, `tool_choice`, `tools`, `text`) while leaving omitted fields absent and never inventing `0` for unknown usage subtotals or sampling defaults.
3. **SDK/CLI Baseline vs Candidate Evaluation & Route Gating (`scripts/check_sdk.py`, `scripts/compatibility_snapshot.py`, `scripts/inspect_model_contracts.py`)**:
   - Added `--allow-candidate-sdk` to `scripts/check_sdk.py` so a candidate SDK evaluation job can run against newer SDK releases while the default CI gate strictly enforces the pinned baseline (`openai==3.37.0`, `anthropic==2.3.0`) and fails closed (`exit 2`) on any version drift or skipped test.
   - Added `cli_version_matrix` (`accepted_baselines` vs `evaluated_candidates`) and `pinned_sdk_baseline` to `scripts/compatibility_snapshot.py`, and re-verified `codex-cli 0.166.0` native `ModelInfo` contract via `scripts/inspect_model_contracts.py`.
   - Verified in `tests/test_p13_schema_drift_ci.py` that route-level feature gating (`assess_route(..., verified=True)`) enforces per-route evidence independently of model-level records or code coverage.
