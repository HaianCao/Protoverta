# Route and policy evidence

Upstream model support and conversion-route support are separate claims. A
successful `/models` response certifies neither. Registry schemas 1 and 2 still
load; their records never implicitly certify a conversion route.

Schema 3 adds `routes` alongside `models`. Each route matches the **exact** API
prefix, upstream protocol/model, inbound `source_protocol`, streaming boolean,
client profile (`strict`, `codex`, `claude`) and the six explicit conversion
policy flags listed below.
There are no wildcard models, policy defaults or cross-provider certificates.

```json
{
  "schema_version": 3,
  "models": [],
  "routes": [{
    "base_url": "https://example.test/api/v1",
    "protocol": "chat",
    "model": "example-model",
    "source_protocol": "responses",
    "streaming": true,
    "client_profile": "codex",
    "policies": {
      "messages_usage_policy": "strict",
      "upstream_reasoning": "provider_default",
      "namespace_tools": "flatten",
      "tool_names": "alias",
      "client_tool_search": "function",
      "public_reasoning": false
    },
    "features": {"text": true, "streaming": true, "tools": true},
    "checked_at": "2026-10-25",
    "evidence": "Example only: replace with an actual reproducible test report",
    "verified_with": "Example only: record the tested core, CLI and SDK versions"
  }]
}
```

This example is not a provider certification. Record the actual policy values
from `route_policies(gateway_config)`; different launchers/native routes can
have different policies. `verified_with` is descriptive evidence, **not** an
automatic installed-version constraint or freshness check. Operators must rerun
certification after relevant provider/model/core/CLI changes and refresh records.
An optional record `identity` and `require_current_evidence` check now bind core
build/provider-profile revisions; `evidence_max_age_days` can enforce freshness.
See [identity and doctor export](advanced-adapters.md). Tokenizer callbacks,
retry/admission settings and SDK versions are not automatically certified. This is an
operator evidence registry, not a cryptographic attestation of the whole runtime.

Missing record/feature means **unknown**; `false` means **unsupported**. Explicit
negative evidence rejects requests even without strict verification. Set
`GatewayConfig(require_verified_routes=True, capabilities=registry)` or
`--require-verified-routes --capabilities-file PATH` to reject unknown routes.
Both upstream model checks and route checks run before upstream I/O. Request
checks use the union of inbound and outbound features, including streaming and
tool options; a text-only test cannot certify a tool turn. Launchers preflight
their baseline streaming route and the gateway checks each actual request.

The library does not add live evidence automatically, enforce age unless selected,
retry unsupported features, alter strict reasoning, or fall back mid-tool turn.
