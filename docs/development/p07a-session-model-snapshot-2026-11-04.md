# P07a — Session model snapshot foundation

Historical foundation checkpoint. The completed contract, including vendor
projections, native pickers, cache names and UX, is recorded in
[P07 completion](p07-catalogs-and-picker-2026-11-04.md). Its status supersedes the
pending-state and provisional response-shape notes below.

Date: 2026-11-04. **P07a implemented; P07 remains IN PROGRESS.** Changes in
this batch are uncommitted for review. P06 was committed first: Protoverta
`fd76a42`, CodexApter `3b0403e`, ClaudApter `75dcf21`. No push/merge/release.

## Scope

The gateway now exposes an explicitly configured, immutable model snapshot at
`GET /v1/models`. Two source launchers configure it using the actual selected
main model plus their configured auxiliary default/role IDs. No provider
discovery, cache migration, global configuration write or default change is
performed while constructing or reading it. Other configured providers are
never scanned. An inherited model not explicitly known to the wrapper is not
invented or advertised.

This closes the local snapshot/auth/resource/isolation foundation. It does NOT
certify interactive harness pickers, aliases, vendor Models schemas or model
capabilities. The existing inference routes, manual model input and explicit
save-default workflow are unchanged. The snapshot is a discovery view, not an
inference allowlist: a manually entered ID still follows existing validation,
route evidence and capability policy.

## HTTP contract

- Exact path `/v1/models`; other GET paths return JSON 404.
- One `Authorization: Bearer <session inbound token>` header is required.
  Missing, wrong, non-ASCII or duplicate Authorization values are rejected.
  This local endpoint does not accept the upstream API key or add a new
  unauthenticated route. X-API-Key-only clients are not certified by P07a.
- No configured snapshot: authenticated 404. Explicit empty snapshot: 200
  with an empty data array. These states are distinct.
- `limit`: decimal integer 1–1000, default 1000. `after_id`: exact ID from the
  current immutable snapshot, URL-encoded as needed. Unknown/blank cursors,
  duplicate/unknown parameters, malformed percent escapes/UTF-8, more than two
  query fields or query length above 2048 characters return 400. Ordering is
  stable in session, with no fuzzy aliasing or case normalization.
- Response: `object: "list"`, `data`, `has_more`, `first_id`, `last_id`.
  Each entry contains exact `id`, `object: "model"`, `display_name` and
  `capability_status: "unknown"`. Absent names remain the exact ID; a caller
  supplying a known display name through SnapshotModel preserves it.
- At most 10,000 unique models and 2 MiB of serialized entry data, validated
  once when constructing the snapshot; bounded page responses. IDs/names are
  1–512 Unicode characters, no controls/surrogates, no whitespace in IDs.
  Duplicate entries are rejected by the primary constructor; the explicit-ID
  convenience constructor deduplicates after validating every entry.
- Snapshot is frozen tuples/frozen entries, bound to the exact upstream API
  prefix and protocol. Attaching it to another upstream is a configuration
  error. Returned page dictionaries cannot mutate the snapshot.
- Standard gateway no-store headers, bounded handler/admission capacity and
  request ID apply. GET consumes no inference/provider quota and sends no
  request upstream. Observations use protocol `models`, without token usage
  or model identity digests; journal readers accept these records.
- Responses never include the snapshot's upstream URL, auth values, other
  providers, capability registry, context/effort metadata or local paths.
  Malformed raw request targets produce a bounded JSON 400 rather than a
  request-thread traceback. Logs remain disabled by the existing handler.

### Vendor compatibility boundary

The local normalized response deliberately omits unknown `created` and
`owned_by`. The [official OpenAI Models list schema](https://developers.openai.com/api/reference/resources/models/methods/list)
includes these fields; its native endpoint does not document our cursor/limit
extension. Therefore this foundation is NOT full strict OpenAI Models schema
conformance. It is also not yet an Anthropic Models response implementation.
No fake provider/model timestamps or ownership are substituted to pass a
strict SDK. Vendor-aware projection/policy and actual interactive harness
behavior must be decided/tested in P07b before claiming picker compatibility.

## Files and validation

Core: `model_snapshot.py`, the new GET handler in `server.py`, diagnostics
protocol support, and `tests/test_model_snapshot.py`. Both launchers pass the
actual selected model to gateway construction; Claude route doctor is updated
too. Per-consumer `tests/test_session_models.py` checks exact configured models,
deduplication, correct upstream binding, no discovery, no other-provider leak
and unchanged defaults/config file. Codex's optional-dependency lifecycle test
stub now explicitly accepts/verifies the snapshot option.

Validation in the existing disposable Windows/Python 3.13.9 preview:

- Full core run: **2070 passed in 308.20s**. This collection preceded the final
  malformed-target handling and supplementary socket/journal/page/admission
  cases. Do not describe it as a full run of the final test collection.
- Final snapshot/diagnostics/observability supplement: **64 passed in 28.62s**.
  It covers the final source, including raw malformed HTTP,
  duplicate auth, concurrent session isolation, active inference capacity,
  missing/empty catalogs, Unicode cursors and the 1000-entry page boundary.
- Codex gateway/session-model/agent regression selection: **45 passed, 1.28s**.
- Claude equivalent selection: **53 passed, 12.94s**, one expected mock-version
  warning. No new full consumer suite claim; P06's full results remain history.
- Independent pinned SDK gate: **26 passed, 26.63s**. This tests inference, not
  strict model-list schema conformance. Ruff with each repo's own config passes;
  configured strict mypy six-module boundary check passes.
- Pinned actual Codex 0.166.0 and Claude Code 2.1.308 each pass the parent A /
  child B Chat-upstream continuation fixture after launcher wiring. Private
  synthetic reports: `p07a-codex-chat.json`, `p07a-claude-chat.json` in the
  existing P00 preview reports directory. These are inference regressions,
  NOT proof that an interactive picker called GET /models.

All testing used fake loopback upstreams, synthetic prompts/keys and temporary
homes/state. No real .env, credentials, installations, PATH, sessions or
free-claude-code changes. An initial malformed-request test was blocked by
Python's client URL validator; the final test sends raw loopback bytes to
exercise the server itself. Routine test stub/import fixes were made before
the final scoped checks.

## Remaining P07b work

1. Resolve vendor-specific list shape with honest unknown metadata, paging,
   auth and native consumers; do not fill required fields with GPT metadata.
2. Inspect pinned Claude interactive model ID filtering. Add reversible aliases
   only if actual evidence requires them; observe exact outbound model routing.
3. Validate pinned Codex catalog schema beyond slug lookup; reject missing
   metadata rather than copying a known model's tool/context profile.
4. Decide explicitly which identity-checked cached discovered IDs may join the
   session snapshot. Keep stale/unknown discovery separate from certification;
   do not introduce startup network requests or silently advertise every model.
5. Exercise both setup UIs and actual harness pickers: provider display name
   save/reload, manual/search paths, empty/error catalogs, per-route unknown vs
   unsupported states, and session-only model changes without default writes.

P08 and later are not started. Review this sub-batch before committing or
continuing, per the established user workflow.
