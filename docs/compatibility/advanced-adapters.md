# Provider integration and route diagnostics

These optional features extend the protocol core without changing the default
strict reasoning contract, editing real CLI sessions, or selecting another model.
No built-in example below is a live provider certification.

## Declarative provider profiles

The existing exact-prefix/protocol/model Chat profile can also encode an explicit
equivalent reasoning-effort field. Example profile fields (synthetic provider):

```json
{
  "base_url": "https://example.test/v1",
  "protocol": "chat",
  "model": "example-model",
  "checked_at": "2026-10-25",
  "evidence": "Replace with the provider's actual equivalent wire contract",
  "chat_token_field": "max_tokens",
  "token_budget_semantics": "completion_total",
  "reasoning_field": "thinking.level",
  "reasoning_values": {"low": 1, "medium": 2, "high": 3},
  "reasoning_semantics": "effort_equivalent"
}
```

Put records under the existing `{ "schema_version": 1, "profiles": [...] }`
wrapper. The example is not a recommended setting for any real provider.
Values must be bounded scalar JSON values and mappings must be injective:
`high` and `max` are never silently collapsed to the same effort. Only explicitly
recorded portable levels are accepted; other levels and conflicting existing
vendor fields reject. Profiles do not decode vendor reasoning output, fabricate
signatures, remove refusals, or authorize arbitrary request postprocessors.

Optional `parameter_aliases` (`{"<tool_name>": {"<source_param>": "<target_param>"}}`)
can be configured on a `ProviderProfile` when a specific provider/model endpoint
rejects non-portable parameter keys. The mapping must be injective with disjoint
source and target parameter sets. It rewrites only top-level `properties` and
`required` entries in the matching tool definition plus top-level keys in the
matching tool's JSON object arguments across request history, completed JSON
responses, and translated SSE tool calls, without modifying arbitrary text,
descriptions, tool outputs, boolean schemas (`false`), or `additionalProperties`.

Catalog policies separately support `collection_field` (null means root list),
`id_field`, bounded `query_params` pairs, and `pagination: "numbered"` in addition
to `none`/`after_id`. Numbered pagination uses `page_param`, `first_page` (0/1),
`total_pages_field`, and optional `current_page_field`. Count changes, mismatched
current pages, contradictory has_more and configured limits reject the refresh.
Provider next URLs remain forbidden. Query parameters cannot override cursors.
Both launcher caches now hash policy-file contents as well as their path, so an
edit at the same path invalidates the cache. Catalog data never certifies support.

## Route-aware doctor

Both preview launchers expose the same checks:

```text
codex-ca doctor -p PROVIDER -m MODEL --allow-live --check text --check streaming --report NEW_REPORT.json
claude-ca doctor -p PROVIDER -m MODEL --allow-live --check tools --check streaming_tools
```

Gateway mode is required for these route checks. Without `--allow-live`, gateway
doctor configures the route but reports NOT TESTED with zero provider requests.
The native legacy doctor remains a separate minimal native probe. No checks run
automatically when setup opens, a catalog refreshes, or a session starts.

Omit `--check` to select all six cases: text, streaming, tools, streaming_tools,
tool_result and history_replay. Full success uses eight short requests, at most
two per case. The probe itself never retries or changes model; explicitly enabled
gateway HTTP retries still follow their configured policy. Synthetic tool output
is a random nonce, executed locally without filesystem/process/network effects.
history_replay sends explicit synthetic conversation history; it does NOT test
the Codex/Claude CLI's actual resume/session-storage behavior.

The local probe HTTP client bounds each response to 2 MiB and its operation to
30 seconds with a 10-second socket timeout. Provider-side requests additionally
use gateway resource limits. Reports contain fixed outcomes and measured durations,
not provider error bodies, prompts, nonces, tool arguments or credentials.

`--report` creates a NEW schema-3 JSON registry file without overwriting an
existing path. Only positive tested features are exported, separately for JSON
and streaming. Failed/network/auth/quota tests remain UNKNOWN, not unsupported.
Passing text does not certify tools; JSON tools do not certify streaming tools.
The report is an operator artifact, never auto-merged into active settings.
`check_route(..., upstream_open=fixture)` is the keyless embedding/test interface.
Custom transports are trusted code and are responsible for their own I/O bounds.

## Evidence freshness and identity

Route records now optionally contain `identity`, a SHA-256 of the core Python
source build and resolved provider-profile contents plus caching/system/budget
conversion settings. The existing six policy flags remain exact selectors.
No API key, inbound token, prompt or absolute installation path enters this hash.
The core hash is cached per process; hot-editing a running interpreter is not a
supported update mechanism. Restart after updates before recording evidence.

`require_current_evidence=True` requires an exact identity match. Optional
`evidence_max_age_days=N` rejects positive evidence older than N days (or dated
in the future). Either option requires a registry and rejects unknown routes even
without a separate require_verified_routes flag. Old schema-3 records remain
usable under legacy policy, but are unknown when a current identity is required.
Explicit negative evidence remains conservative until an operator replaces it.

Identity covers core/profile conversion behavior, not provider drift, CLI/SDK
versions, tokenizer correctness, network/auth configuration or every runtime
option. Those require separate evidence. It is not a remote attestation/signature.

## Session diagnostics in launcher setup

Set `gateway_runtime = { diagnostics_dir = "diagnostics" }` in a preview launcher
or use its advanced setup form. Relative directories resolve beside the config.
Journals are separated by validated provider ID and random session filename.
Reopen setup and click **Last session diagnostics** to inspect the latest report.
Run continues to close the setup server and give the terminal to the CLI.

Each journal contains fixed request observations only: local request ID, protocols,
HTTP status, final outcome, elapsed time, first-payload latency and measured
input/output/cache token counts when available. Missing counts stay unknown,
never inferred as zero. First payload is a socket-write timing, not model TTFT
or proof the client received a token. Native SSE usage may be unavailable because
its validator deliberately does not decode every provider feature payload.

Per-session writing stops at 1,024 records/1 MiB and rejects oversized lines.
New sessions best-effort prune older owned journals, keeping 32 including the new
one; concurrent/locked active files can temporarily exceed this retention target.
Non-journal files and symlinks are not deletion targets. The reader returns at most
100 validated records and filters unknown/invalid fields. This is opt-in local
retention: operators own directory permissions and cleanup. No default file is
created, and an unavailable diagnostics directory does not prevent CLI startup.

The journal producer uses a nonblocking queue of at most 128 records. A daemon
writer owns the file; session shutdown waits at most 250 ms for it, without
closing a stream under a blocked filesystem call. Delivery is best-effort. A
completed journal summary reports `dropped_records`; a blocked/crashed writer
may not write this summary. Directory maintenance/read scans at most 512 entries,
including unrelated files. `limited=true` warns when the scan was capped or
records were dropped/truncated. A crowded directory returns the newest qualifying
file in that bounded sample, not a guaranteed globally newest file. Use a dedicated
local directory. Initial directory/header setup and directory reads still require
a responsive filesystem; no network filesystem timeout guarantee is made.

No prompt, answer, reasoning, tool schema/argument/output, raw URL, provider trace
ID, key or exception text is retained. HTTP 200 followed by invalid_stream is
shown as a failed stream. Disabling diagnostics does not delete previous reports.
