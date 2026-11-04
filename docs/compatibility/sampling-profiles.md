# Explicit sampling defaults

`temperature` and `top_p` supplied by callers are preserved on supported
translated routes. Protoverta does not assume a provider default when a caller
omits either value. Some models reject sampling controls entirely.

An operator can add `sampling_defaults` to an existing Chat `ProviderProfile`:

```json
{
  "schema_version": 1,
  "profiles": [{
    "base_url": "https://example.invalid/v1",
    "protocol": "chat",
    "model": "example-model",
    "checked_at": "2026-11-04",
    "evidence": "Replace with reviewed documentation or an authorized probe",
    "chat_token_field": "max_completion_tokens",
    "token_budget_semantics": "completion_total",
    "sampling_defaults": {"temperature": 0.7, "top_p": 0.9}
  }]
}
```

This is a synthetic configuration example, not evidence for a real provider.
Use the existing `provider_profiles_file` setting in either preview launcher,
or `ProviderProfiles.load(path)` in an embedding. No new setup-page choice is
required. The record must certify its existing token-budget contract as well.

Matching is exact on normalized base URL prefix, Chat protocol and model ID.
Each configured value must be a finite number: temperature in `[0, 2]`, top_p
in `[0, 1]`. Booleans, strings, unknown keys and empty mappings are rejected.
Only declared keys are defaulted; missing/null request values receive the
configured value. Explicit values, including zero, always win. Input objects are
copied; changing a profile changes the evidence fingerprint.

The gateway sends these values on the real outgoing request and projects them
into every generated Responses JSON/SSE snapshot. A capability record explicitly
rejecting the applied control prevents upstream I/O. A provider rejection is not
silently retried with the setting removed, a different model, or a lower value.
An evidence string is an operator assertion, not automatic certification.

For sampling contracts independent of Chat token-field quirks, use a
[`kind: generation` record](generation-profiles.md). This supports Chat,
Messages and Responses upstreams. Messages temperature is restricted to `[0, 1]`.
No defaults are inferred for any protocol. An explicitly configured sampling
profile can modify native forwarding requests too; native response passthrough
remains unchanged. Existing Chat records retain their token-budget contract.
Sampling defaults alone do not establish full OpenResponses conformance.
