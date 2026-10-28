# Bounded catalog discovery

`fetch_catalog(base_url, headers, CatalogPolicy(...))` discovers model IDs and
reported names. Returned `ModelInfo` also supports explicitly mapped context and
output limits and input modalities. Every entry has `capability_status="unknown"`.
Reported metadata is not promoted into the capability registry or used as a
certified context limit automatically.

The API prefix is preserved exactly. The default path is `/models`; pagination
is disabled unless `pagination="after_id"` is selected. Provider-supplied next
URLs are never followed. Redirects are refused, including same-host redirects;
keys are not forwarded to a redirected location.

Defaults bound the whole operation to 30 seconds, 8 MiB cumulative response
bytes, 100 pages and 10,000 distinct models. Socket reads share the total deadline;
duplicate conflicting metadata, repeated cursors, unconfigured pagination,
invalid IDs/metadata, and exceeded bounds fail the entire refresh. Partial results
are not saved as a successful catalog. Embedding transports must honor the budget
attached to the request; arbitrary custom blocking callbacks cannot be forcibly
interrupted by this synchronous library.

Provider-specific mappings require an explicit, evidence-scoped JSON policy:

```json
{
  "schema_version": 1,
  "base_url": "https://example.test/api/v1",
  "checked_at": "2026-10-25",
  "evidence": "Example only: cite the provider's actual model catalog specification",
  "policy": {
    "pagination": "none",
    "name_field": "name",
    "context_field": "context_length",
    "output_field": "limits.max_output_tokens",
    "modalities_field": "architecture.input_modalities"
  }
}
```

Field paths are bounded dot-separated object keys, not expressions. Missing
metadata remains unknown; a present malformed value fails validation. Mapping
evidence is operator-maintained and is not a claim that every provider uses these
fields. Policy files are limited to 64 KiB and match one exact prefix. Existing
inference `ProviderProfiles` remain separately evidence-scoped; discovery does
not authorize inference rewrites. Extended listing fields and numbered pagination
are documented in [declarative provider integration](advanced-adapters.md).

Both launcher previews use this implementation for **gateway-mode** discovery.
`gateway_runtime = { catalog_policy_file = "catalog.json" }` resolves relative to
the launcher config file. Native direct-only mode keeps its dependency-free
discovery. Catalog caches bind to prefix, protocol, auth mode, credential
fingerprint, policy path and bounded policy-file content hash (Codex also includes
its discovery path/secret ref). Key changes and policy edits invalidate caches
without storing raw credentials. Metadata is stored separately in `model_info`
and model selection still accepts manual IDs. A discovery error does not prevent
manual selection or change the last successful cache.

## Messages version headers

The core discovery API does not infer a protocol from a URL or add an Anthropic
header globally: the caller supplies the request headers. Each pagination request
preserves that header snapshot. Both launcher previews explicitly send
`anthropic-version: 2023-06-01` when the selected provider protocol is Messages,
independently of bearer versus API-key authentication. Chat and Responses
discovery do not receive that header. CodexApter also scopes its Messages cache
identity to the selected version, invalidating older headerless discovery data.

This pin matches the existing Messages inference contract; it is not automatic
version negotiation. Anthropic's [versioning guide](https://platform.claude.com/docs/en/api/versioning)
requires an explicit header and documents breaking changes between API versions,
while the [Models reference](https://platform.claude.com/docs/en/api/models/list)
currently labels the listing header optional. Do not infer that every provider
rejects a headerless list request, or silently upgrade the entire protocol to a
new version from discovery alone. A newer version needs a separate contract and
compatibility review.

Loopback tests exercise a fixture that requires the selected header/auth on each
page and preserves an API prefix. They do not certify Anthropic or third-party
live endpoints, token scopes, or models' inference capabilities.
