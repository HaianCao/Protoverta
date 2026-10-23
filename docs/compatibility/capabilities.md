# Recorded provider/model capabilities

`CapabilityRegistry` holds operator-recorded observations for an exact API
prefix, upstream protocol and model ID. It does not infer support from a model
name, an HTTP 200, or a model-catalog entry. Records include an ISO `checked_at`
date and a documentation/probe `evidence` description. Entries do not expire
automatically; operators must refresh observations when providers change.

Each feature is true, false, or unknown (omitted). Prefix hostnames are normalized
case-insensitively and trailing slashes are removed; paths, model IDs and
protocols remain distinct. No wildcard, cross-protocol or cross-vendor inheritance
is performed. Duplicate entries and URLs containing credentials/query/fragment
are rejected. The JSON registry is bounded at 2 MiB and must not contain secrets.

See [the registry example](../../configs/capabilities.example.json) for its
versioned JSON format. The example is illustrative, not verified provider data.

```python
from protoverta import CapabilityRegistry

registry = CapabilityRegistry.load("capabilities.json")
report = registry.preflight(
    "https://provider.example/v1", "chat",
    {"model": "your-model-id", "stream": True,
     "messages": [{"role": "user", "content": "Hello"}]},
    require_verified=True,
)
print(report.supported, report.unknown, report.unsupported)
```

By default preflight blocks explicit false entries and reports unknown features.
`require_verified=True` additionally blocks missing observations. A report
contains only feature names, not prompts, credentials or response bodies.
`request_features()` inspects outbound options; it does not validate or convert
the request itself. Convert first when using the library directly.

The gateway checks the final outbound request after compatibility profiles,
conversion, and reasoning overrides, before any upstream network call. This
applies to both native and translated requests:

```text
--capabilities-file capabilities.json
--require-verified-capabilities
```

The Python options are `GatewayConfig(..., capabilities=registry,
require_verified_capabilities=True)`. Omitting the registry keeps existing
behavior. Requiring verified capabilities without a registry is an error.

Recognized features: `text`, `streaming`, `tools`, `forced_tool_choice`,
`json_object`, `json_schema`, `strict_json_schema`, `reasoning_effort`, `images`,
and `mid_conversation_system`. Feature detection inspects content, not arbitrary
tool schema properties. Records cannot enable conversions the core does not
implement: for example, an image-capable model still cannot receive translated
images in the current text/function subset.

Both consumer development branches expose the registry path and strict option
in their provider editors. Paths are resolved relative to the configuration
file, not the working directory. Basic launch checks run before setup hands off
the terminal; each actual outbound request is checked again for tools, formats
and other recognized features. The editors reference a registry file; they do
not create observations or claim support from the model catalog. These changes
require the next pinned core release before consumer preview publication.
