# Effective tool policy and explicit sampling defaults

## Behavior

Translated HTTP requests establish their tool policy before upstream I/O. Missing
choice becomes auto with active tools, none without tools. Missing parallel
permission is enabled with tools, disabled without them; Messages none forbids
all calls. Explicit choices/permissions are preserved where representable.
Forced unknown tools and required choice without an active catalog fail early.
No-tool requests omit wire flags while the gateway locally enforces zero calls.

Each response is checked against the actual outbound tool names and kinds.
Checks precede alias/name restoration, search remapping and Messages block
serialization. Wrong/unknown calls, forbidden calls, a second call with parallel
permission disabled, and ordinary completion without a required call fail with
`tool_policy_violation`. Length-limited output remains incomplete. JSON rejects
before returning a response; SSE rejects before forwarding an offending tool
frame and never emits a success terminal after an error. Before headers the
response is non-retryable 502; late errors retain the already committed HTTP
status. No automatic replay or tool execution is introduced.

Generated Responses JSON/SSE snapshots share effective request provenance:
logical tool names/namespaces, loaded search definitions plus client search,
effective choice and parallel permission. Inactive deferred tools and internal
search pseudo-functions are absent. Bare conversion APIs still do not invent
unknown request policy; native passthrough retains its upstream contract.

Chat `ProviderProfile.sampling_defaults` optionally supplies temperature/top_p
under an exact endpoint/protocol/model evidence record. Missing/null request
values receive configured defaults; explicit values including zero win.
Defaults are actually sent and reflected in snapshots. Invalid mappings and
values fail configuration validation; explicitly unsupported capabilities block
I/O. No default is globally invented or inferred from catalog membership.
Profile fingerprints include the new configuration. Messages/Responses profile
defaults are not implemented; explicit portable sampling remains preserved.

## Test design

New regression tests cover six directed routes, JSON/SSE, forced choices,
unknown/wrong-kind calls, no-tools/none, required completion, parallel=false,
late SSE failure, incomplete output, namespace/alias restoration, effective
client tool-search catalogs and pre-serialization enforcement. Sampling tests
cover malformed/nonfinite/default values, exact matching, immutable copied
records, explicit zero precedence, wire/snapshot agreement and negative model
capability preflight. The entire core suite additionally exercises request-lane
isolation, cancellation accounting, retry boundaries and resource limits.

Legacy SDK, doctor, request-isolation and heartbeat fixtures that emitted tools
must now actually offer those tools. Doctor fixtures distinguish forced choice
from normalized auto/none. SDK default-policy coverage checks the created event;
separate strict Messages reasoning-usage negatives remain. No skips or xfails
were added and no external validator was changed.

Final counts, source/wheel hashes, gate outcomes and limitations are recorded in
[validation receipt](tool-policy-validation-2026-11-04.json). All provider I/O was
synthetic loopback; these checks are not live provider certification. Real user
installations, credentials, configs, sessions and PATH were not changed.

## Remaining full-schema gap

The pinned OpenResponses audit still fails 12/12 selected cases, with 78 field
occurrences: temperature/top_p 24, serving tier 12, Messages reasoning usage 6,
and penalty/logprob controls 36. No sampling profile is enabled in that baseline.
Do not replace unavailable tier/usage with guessed defaults or zero. Further
controls require protocol-specific semantics and a supported-route matrix.
See [audit](openresponses-audit.md) and [sampling profiles](../compatibility/sampling-profiles.md).
