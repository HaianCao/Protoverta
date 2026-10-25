# Explicit session routing and local token quotas

Both policies are opt-in. They do not select another model, compress history,
fabricate reasoning state or change a tool call/result. Independent gateways
retain independent listeners, credentials, routing choices and session IDs.

## OpenRouter Chat provider pin

`GatewayConfig(openrouter_provider="<provider-or-endpoint-slug>", ...)` or
`--openrouter-provider` is accepted only with Chat upstream protocol and the exact
prefix `https://openrouter.ai/api/v1`. Launcher gateway-mode TOML can set
`gateway_runtime = { openrouter_provider = "<slug>" }`.

Each request receives `provider.only/order=[slug]`, `allow_fallbacks=false`,
`require_parameters=true`, plus a random `session_id` stable for that gateway
lifetime. Conflicting native request routing is rejected, never overwritten.
Routing enters evidence identity; ephemeral session IDs do not. No slug is chosen
automatically and no model inference is needed to turn this on.

Use the provider's documented full endpoint slug to narrow endpoint variants.
A base provider slug may match multiple endpoints. This is a provider routing
restriction, not proof that an opaque item belongs to the selected Azure resource,
or a way to migrate signed/encrypted reasoning. Restarting a gateway creates a new
session ID. Sticky routing alone can fall back; this policy explicitly disables
fallbacks. Responses/Messages vendor routing extensions are not inferred from Chat.

Sources: [OpenRouter provider selection](https://openrouter.ai/docs/guides/routing/provider-selection)
and [session affinity](https://openrouter.ai/blog/tutorials/prompt-caching-sticky-routing/).

## Per-model token reservation (embedding API)

Set `shared_token_limit=<positive total tokens per window, at most 2**63-1>`, `shared_admission_db`,
and an explicit `token_counter` callback returning `TokenCount(..., exact=True)`.
The existing `shared_window` defines the window. This embedding-only API does not
load/download a tokenizer or import arbitrary callback code from CLI/TOML. The
counter must cover the complete model-specific input including tools/system/history;
its exactness is asserted by the operator, not discovered by Protoverta.

Before each upstream generation attempt, a SQLite transaction reserves exact input
count plus the request's explicit positive output cap. Ambiguous or missing caps,
estimated counts and opaque compaction inputs fail before generation. Limits are
scoped to exact API prefix, credential hash and model hash, across gateway sessions.
The database contains hashes, amounts and timing only. A conflicting policy for
that scope rejects instead of silently choosing a limit. Existing RPM/concurrency
admission remains independently enforced.

After a successful validated response with complete reported usage, the reservation
is reconciled to input plus output tokens. No cached-token discount is guessed.
Missing usage, partial output, explicit rejection and ambiguous transport failure
retain the full reservation conservatively. Every retry reserves separately.
Cleanup starts a fresh window; active reservations survive their original window
until reconciliation or a finite crash lease expires. Brief conservative 429s can
occur while another handler finishes cleanup. This is local admission, not a
guarantee of provider billing/rate-limit compliance: trusted counters or upstream
usage may be wrong, and usage can exceed a reservation. Count API calls consume
request admission but are not generation token reservations.
Extremely large reported usage saturates internal SQLite accounting at `2**63-1`,
which exhausts every allowed quota; returned protocol usage is not modified.

This follows the reserve/reconcile pattern documented by
[LiteLLM](https://github.com/BerriAI/litellm-docs/blob/main/docs/proxy/io_token_rate_limits.md),
without adopting automatic output caps, cached-token discounts or failover.
