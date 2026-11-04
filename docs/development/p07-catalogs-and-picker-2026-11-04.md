# P07 — Session catalogs, native pickers and setup UX

Date: 2026-11-04. **P07 COMPLETE within the declared versioned scope.** This
closes P07a and P07b together. The user requested completion of all remaining
P07 work without intermediate review stops. P07 was committed as core
`6891699`, Codex `f37c2fc`, and Claude `f68d661` (following P06 at core
`fd76a42`, Codex `3b0403e`, Claude `75dcf21`). No push, merge or release.

## Acceptance map

| Plan requirement | Implementation and evidence | Result |
|---|---|---|
| Authenticated bounded GET /v1/models | Immutable upstream-bound snapshot, strict query/auth handling, page/entry/byte limits; normalized and explicit vendor projections; raw socket and SDK tests | PASS |
| No repeated provider discovery from harness | GET reads only memory; launch reads only local names for explicit session IDs; pinned Codex gateway disables API-key remote catalog discovery | PASS |
| Single-upstream isolation | Snapshot constructor matches exact prefix/protocol; launchers never scan another provider or add its IDs; token/cross-gateway tests | PASS |
| Claude ID filtering/aliases | Actual 2.1.308 TUI lists exact slash/colon IDs via session-only modelPicker; selects B, dispatches B; no alias or behavesAs capability substitution | PASS in pinned profile |
| Codex catalog schema | Native 0.166.0 remove-one-field parser inspection identifies 10 required fields; launcher checks required/nested types and version; no generated GPT metadata | PASS in explicit operator-catalog profile |
| Display name, persistence, search/manual, empty/error catalogs | Both actual Chromium setup flows, authenticated reopen, ID/name distinction, XSS-like text, 20 entries, empty/failed discovery and manual Run | PASS |
| Verified/unverified/unsupported route states | Read-only exact text-stream evidence checks; explicit route/policy match; unknown remains unknown; negative evidence survives freshness requirements | PASS for stated feature scope |
| No implicit global default change | Web Run no longer saves defaults; only Save as default writes them. Native Codex change leaves wrapper config unchanged; Claude session-only picker leaves native and wrapper model defaults unchanged | PASS |

## Session snapshot and vendor projections

`ModelSnapshot` owns frozen `SnapshotModel` entries. It is bound to the exact
upstream API prefix and protocol, with at most 10,000 unique models and 2 MiB
of serialized entry metadata. IDs/names are validated Unicode scalar strings,
1–512 characters; IDs cannot contain whitespace. Page output is separately
bounded to 2 MiB. Cursor ordering is stable for the lifetime of that snapshot.

`GET /v1/models` accepts exactly one session Bearer Authorization header OR one
session x-api-key header. Mixed/duplicate credentials are rejected even if
their values agree. The upstream key is not accepted as the local credential.
No snapshot returns authenticated 404; an explicit empty snapshot returns 200
with an empty array. It shares local admission capacity with inference, but
sends no provider request and spends no inference/provider quota. Existing
no-store/request-ID/error boundaries and shutdown behavior apply. Diagnostics
use protocol `models`, without fabricated inference tokens/model digests.

The local default format is `normalized`: object/list, data, directional
has_more and first_id/last_id. Entry fields are exact ID, display_name,
object/model and capability_status=unknown. `format` explicitly selects
normalized, messages or openai. `limit` is 1–1000; default 1000 for normalized
and OpenAI projections, 20 for Messages. Exact after_id OR before_id is allowed,
never both. Unknown cursors, duplicate/unknown query keys, malformed encodings,
too many fields and query length above 2048 fail locally.

The [OpenAI model-list reference](https://developers.openai.com/api/reference/resources/models/methods/list)
requires a creation timestamp and owner. The OpenAI projection therefore
requires explicitly reported `created`/`owned_by`; missing facts produce a
nonretryable 422 and recommend normalized format. No zero timestamp or owner
is invented to make an unknown entry pass. Pagination parameters on this local
projection are an explicit extension; OpenAI SDK automatic pagination is not
claimed. The SDK gate validates known entries and the unknown-field rejection.

An anthropic-version header selects Messages format unless format is explicit.
The 2023-06-01 listing contract provides type/model, exact ID, display_name and
created_at. The [Messages model-list reference](https://platform.claude.com/docs/en/api/models/list)
permits epoch for an unknown release date. API 2026-10-21 additionally requires
reported lifecycle; unknown lifecycle fails with nonretryable 422, while unknown
nullable capability/context/line/retirement metadata stays null. A lifecycle
value is validated, not inferred from a model name or presence in discovery.
Other Messages listing versions are rejected explicitly. This does not upgrade
the Messages inference version. Strict raw SDK model validation and actual
Anthropic SDK auto-pagination pass with explicitly reported fixture metadata.

Launchers currently supply only IDs/names, so a strict projection needing
additional facts can legitimately fail. Listing a model is not certification,
and an empty list never authorizes bypassing policy. Manual inference IDs still
follow the existing capability/route checks: discovery is not an allowlist.

## Cache and setup behavior

Only the selected main model and explicitly configured auxiliary/role model
IDs enter the session snapshot. Fresh cache data may enrich their display names
only when prefix, protocol, credential fingerprint and existing policy identity
match. Stale, malformed, oversized or mismatched cache data falls back to exact
IDs. The bounded name reader does not migrate/write caches, fetch models, copy
capabilities or promote every discovered ID into a session. Existing provider
refresh remains the explicit/visible setup discovery operation.

Codex setup now searches both IDs and display names, renders names/IDs with
textContent and handles prototype-like IDs such as constructor safely. Provider
display names remain distinct from immutable IDs and survive save/reopen.
Both setups show a separate route evidence hint. Verified covers only exact
text+streaming evidence for that route/profile/policy; tools, effort and context
remain separate Run checks. Missing/incompatible evidence is unverified;
known negative evidence is unsupported. When freshness/current identity is
required, the UI conservatively withholds a positive label instead of inventing
a current certification. All status checks are read-only and keyless.

Run previously persisted the current selection in both setup servers. It now
validates and hands the terminal over without saving defaults. Save as default
is the explicit persistence action. Browser acceptance saves model-19, reopens
setup, then runs a manual model; the saved model-19 remains unchanged.

The browser intentionally removes its bootstrap token from history. Tests
reopen the authenticated setup URL rather than treating a token-free browser
reload as authenticated. No cookie/auth weakening was introduced for testing.

## Pinned native picker behavior

### Codex 0.166.0

`CODEX_CA_MODEL_CATALOG` remains explicit operator input. The launcher checks
version and the native required record structure before passing the file to
Codex. The required fields observed by the actual parser are slug, display_name,
supported_reasoning_levels, shell_type, visibility, supported_in_api, priority,
support_verbosity, truncation_policy and experimental_supported_tools. The
preflight also checks reasoning membership/duplicates, truncation, context
consistency, relevant types and JSON validity. Duplicate JSON keys, NaN,
non-Unicode data, too many records and files over 2 MiB are rejected. Native
Codex remains the final parser for optional/version-specific fields; this is
not a complete replacement for its Rust deserializer.

No production code reads bundled GPT metadata to fill an unknown model's
context, tools or instructions. The developer fixture uses synthetic reviewed
records based on the pinned binary's format and disables unsupported fixture
features; that is not provider certification. Native TUI selects Synthetic Beta
and the conversation dispatches exact vendor/beta:free. For the pinned gateway
profile, API-key remote catalog discovery is disabled to avoid sending a local
session credential to an inherited remote catalog URL. Local file catalogs
retain precedence. Other versions/direct modes are not newly certified here.

### Claude Code 2.1.308

The [native modelPicker setting](https://code.claude.com/docs/en/settings-reference#modelpicker)
accepts explicit model IDs and labels through process settings. The launcher
uses that mechanism on the pinned gateway profile, listing only configured
session IDs and replacing the built-in lineup. It supplies neither behavesAs
nor an availableModels override; managed access restrictions retain native
precedence. No reversible alias was needed for the tested slash/colon IDs.
Unknown versions retain their previous behavior rather than receiving an
unverified picker override. Direct mode remains native.

The native picker retains its own Default/current entries and managed-policy
behavior. Only the explicit configured rows are certified by this test, not an
arbitrary native alias or provider-owned model. Press **s** to use the selected
row for this session only. Enter or `/model ID` explicitly saves a native
default; the launcher does not intercept that native action or rewrite user
files afterward. The session-only test checks native model-default values and
wrapper config before/after. A native title helper can use initial model A even
after the conversation switches to B; the fixture classifies it separately
and rejects conversation traffic that silently falls back from B to A.

## Validation and reproduction

All acceptance used disposable Windows/Python 3.13.9 state, synthetic prompts,
fake credentials and loopback upstreams. Native binaries were Codex 0.166.0 and
Claude Code 2.1.308; source wrapper builders supplied the actual arguments.
No installation, global PATH/env, real config/session, .env/provider key,
free-claude-code, or remote repository was modified by tests.

Full source suite checkpoints: core **2087 passed (316.59s)**; Codex **389 + 12
subtests (370.97s)**; Claude **424 (353.77s)** with five expected mock-version
warnings. Small final boundary changes/tests followed collection; final focused
checks are **core 78 (40.42s)**, **Codex 52 (2.39s)**, **Claude 22 (1.65s)**.
Do not add those totals together or invent a full final-collection run. The
independent pinned SDK gate has **29 passed (27.46s)**. Ruff is run separately using each
repo's own config; strict mypy covers the six existing configured boundaries.
All outcomes are recorded in the latest handoff after final completion.

Both real Chromium flows pass desktop/mobile, light/dark, literal XSS-like
names, name search, exact ID selection, empty/error catalogs, authenticated
reopen, explicit default persistence and real launcher/gateway foreground
handoff to a synthetic CLI child. Desktop/mobile screenshots were inspected.
Native ConPTY tests separately prove both real `/model` pickers; the browser
handoff is not presented as native TUI evidence. Six real parent/child live
continuation regressions across Chat/Messages/Responses also pass after the
catalog changes. P06's closed-child resume limitation remains unchanged.

Reproduction from the core checkout, using an isolated preview Python and
temporary report paths:

```powershell
& $PreviewPython -m pip install -e '.[test,sdk-test,browser-test,harness-test]'
& $PreviewPython -B scripts/check_sdk.py
& $PreviewPython -B scripts/inspect_model_contracts.py --report "$Reports/native-contract.json"
& $PreviewPython -B scripts/smoke_model_picker.py --client codex --source ../free-codex-cli --report "$Reports/codex-picker.json"
& $PreviewPython -B scripts/smoke_model_picker.py --client claude --source ../free-claude-cli --report "$Reports/claude-picker.json"
```

The TUI driver requires Windows and pywinpty 3.1.2 (optional harness-test extra).
It creates only its own empty temporary workspace, uses a bounded outer job
and a nested job for the returned PTY child handle, separates text/key events,
waits for complete native dialog boundaries, and cleans owned state after job
termination. Exploratory harness-flag/keystroke/encoding/cleanup failures were
fixed; they were not reclassified as product passes. Two exact leftover failed
probe directories were verified under Temp and removed after their jobs exited.

Private synthetic artifacts in the existing P00 preview reports directory:
`p07-codex-native-contract.json`, `p07-{codex|claude}-picker.json`,
`p07-{codex|claude}-browser/` and
`p07-final-{codex|claude}-{chat|messages|responses}-agents.json`.
No live provider, wheel/remote CI, new native CLI version, arbitrary managed
policy, or persisted/closed child resume certification is claimed. P08 is next.
