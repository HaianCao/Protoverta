# Evidence-scoped generation profiles

`GenerationProfile` declares sampling defaults without requiring Chat-specific
token-field or reasoning mappings. JSON files loaded by `ProviderProfiles.load`
retain `schema_version: 1`; new records must specify `kind: "generation"`.
Records without `kind` retain the existing Chat `ProviderProfile` format.

```json
{
  "schema_version": 1,
  "profiles": [{
    "kind": "generation",
    "base_url": "https://example.invalid/v1",
    "protocol": "messages",
    "model": "example-model",
    "checked_at": "2026-11-04",
    "evidence": "Replace with reviewed provider/model documentation or authorized probe",
    "sampling_defaults": {"temperature": 0.7, "top_p": 0.9}
  }]
}
```

This is synthetic configuration, not a verified real-provider profile. Connect
it with the existing `provider_profiles_file` launcher setting or a gateway's
`provider_profiles=ProviderProfiles.load(path)`. No additional setup-page control
is required. A record is an operator assertion, not automatic certification.

Matching uses the exact normalized base URL prefix, upstream protocol and model.
Only one record may occupy that tuple. Existing Chat records can already combine
sampling defaults with token/reasoning/tool quirks; do not add a second record for
the same tuple. Profile changes alter the evidence fingerprint.

Defaults accept only finite numeric `temperature` and `top_p`, respectively
`[0, 2]` and `[0, 1]`. Messages temperature must be `[0, 1]`. Booleans, nulls,
unknown keys and empty mappings are invalid. A configured key fills a missing or
null request value; explicit values including zero win. Profiles are immutable
and do not mutate caller objects. A capability explicitly rejecting a control
still prevents upstream I/O. Unsupported controls are not silently removed.

Outgoing requests carry the applied defaults; generated Responses JSON/SSE
snapshots expose the effective values. Explicit sampling profiles can change
native requests too; native responses are still passed through. Profiles do not
invent reasoning, usage, service tiers or token probabilities.

## Opt-in Responses penalty dialect

A generation record with `protocol: "responses"` may additionally declare
`"responses_penalties": true`. Supply evidence for that exact provider/model's
equivalent `frequency_penalty` / `presence_penalty` semantics before enabling it.
This forwards explicit incoming values in `[-2, 2]`, including zero. It does not
insert defaults or enable penalties for Messages. It does not imply standard
OpenAI Responses support. With no exact contract, translated Chat-to-Responses
penalties remain rejected before I/O. A penalty-only record needs no sampling
defaults; a record declaring neither contract is invalid.

Bare `convert_request` users can explicitly pass `responses_penalties=True` for a
Responses target. They own the provider evidence and outgoing request handling;
the gateway derives the flag from its exact configured profile. No discovery,
hostname heuristic, automatic live probing or protocol fallback enables it.

These contracts do not establish full OpenResponses conformance. Missing values
remain unknown rather than being replaced with schema-satisfying guesses.
