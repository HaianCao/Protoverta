# Configuration files

## Capability registry

`--capabilities-file FILE` loads a JSON registry of recorded capabilities for exact API prefixes, protocols and model IDs. Unsupported features are rejected before the request is sent; `--require-verified-capabilities` also rejects features the registry does not know. The format and its versions are described in [capabilities](compatibility/capabilities.md) and [route evidence](compatibility/route-evidence.md).

```{literalinclude} ../configs/capabilities.example.json
:language: json
:caption: configs/capabilities.example.json
```

## Provider profiles

`--provider-profiles-file FILE` loads evidence-scoped wire options for exact provider models (parameter aliases, sampling and generation profiles). Profiles never downgrade semantics. See [generation profiles](compatibility/generation-profiles.md) and [sampling profiles](compatibility/sampling-profiles.md).

## Shared budgets

`--shared-admission-db PATH` with `--shared-requests`, `--shared-window` and `--shared-concurrency` lets several gateway processes share one provider account's request budget through a dedicated SQLite file. See [routing and token quotas](compatibility/routing-and-token-quotas.md).
