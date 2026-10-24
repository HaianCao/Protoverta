# Provider profiles, shared budgets and retry boundaries

These options are opt-in source features. They are not present in the previously
published alpha-6 wheel. Test matching source/wheels together; do not overwrite
an existing release asset with a same-version local validation build.

## Provider wire profiles

Use --provider-profiles-file FILE, or GatewayConfig(provider_profiles=...).
ProviderProfiles.load() reads a bounded version-1 JSON document:

```json
{
  "schema_version": 1,
  "profiles": [{
    "base_url": "https://provider.example/v1",
    "protocol": "chat",
    "model": "exact-model-id",
    "checked_at": "2026-10-24",
    "evidence": "REPLACE with provider documentation and a passing budget probe",
    "chat_token_field": "max_tokens",
    "token_budget_semantics": "completion_total"
  }]
}
```

This example is NOT a real model certification. A profile selects max_tokens
or max_completion_tokens only after the operator has established equivalent
completion-total budget semantics for this exact provider/model. Legacy APIs
can count reasoning differently: never infer equivalence from field spelling.
Unknown models/prefixes are unchanged. Conflicting token fields fail closed.
Profiles never strip schema constraints, prompts, tools or reasoning controls.
They are applied before capability preflight, on native and translated routes.
No wildcard inheritance, automatic downgrade, URL guessing or model fallback.

## Cross-process budgets

--shared-admission-db FILE enables a dedicated local SQLite database. All
gateway processes to coordinate must use the SAME absolute database path and
matching shared_requests/shared_window/shared_concurrency policy. Defaults are
40 attempts per 60 seconds and 5 concurrent upstream requests. Without a
database configured, existing per-gateway behavior is unchanged.

Scope is a one-way fingerprint of the normalized exact API prefix and upstream
credential, independent of inbound protocol or requested model. Prompts, tool
arguments, responses and credentials are never stored. Different keys/prefixes
do not inherit budgets; aliases, vendor account-wide limits and quotas shared
across multiple keys cannot be inferred automatically.

SQLite transactions enforce admission across threads/processes. Rejected work
gets HTTP 429 and Retry-After without reaching upstream. Each retry consumes a
new request attempt; release does not refund the rolling-window quota.
Upstream 429/503 Retry-After creates a shared cooldown. A conflicting policy or
unusable database fails with 503 rather than bypassing protections.

Leases cover total_timeout + timeout + 5 seconds and expire after a crashed
process. Normal completion, errors and observed disconnect release concurrency.
Clock changes and uninterruptible OS DNS can affect lease timing. This is not
an exact token-per-minute limiter or an automatic vendor quota detector.
Use a dedicated writable local disk, not a network/synchronized filesystem.
Changing a published scope policy requires deliberate operator coordination;
the gateway never deletes another process's database or resets its quota.

## Bounded retries

--retry-attempts defaults to 1 (OFF), maximum 4. --retry-max-wait defaults to
5 seconds. Only explicit upstream HTTP 429/502/503/504 rejection from opening
the request is eligible. Retry-After is honored; a wait exceeding the configured
maximum or remaining request budget returns the error instead of retrying early.
x-should-retry: false prohibits retry. Shared quota applies to every attempt.

No automatic retries of ambiguous transport failures, schema/format errors,
auth/permission failures, unsupported reasoning, partially read bodies or
partially delivered streams. No model/provider switch or repaired tool history.
Retrying an explicit rejection can still be billable according to the provider;
this is an operator choice, not an exactly-once delivery guarantee.

## Client lifetime

For an authenticated, parsed request, the gateway watches the local client
connection. EOF/reset cancels its request budget and shuts down registered
default HTTP(S) sockets, including reads waiting for upstream headers/body.
Clients must keep their write side open while awaiting the response: deliberately
half-closing the request connection is treated as cancellation.

Cancellation does not forcibly interrupt OS DNS or arbitrary injected transports.
Injected transports must implement bounded reads/cancellation themselves.
The gateway never kills unrelated processes. Existing stream validation and
terminal-at-EOF rules remain in force.

## Consumer configuration

Both integration launchers accept an optional gateway_runtime TOML inline table:

```toml
gateway_runtime = { shared_admission_db = "C:/path/shared-budget.sqlite", shared_requests = 40, shared_concurrency = 5, retry_attempts = 2, provider_profiles_file = "profiles.json" }
```

Paths are relative to the provider config file, not the terminal directory.
Use an absolute shared database path to coordinate CodexApter and ClaudApter.
Their setup editors expose these optional settings. Loading/saving config does
not create a database, make probes or update observations. Direct mode rejects
runtime policies and remains independent of the optional core dependency.
