# Provider certification is separate from local conformance

No new provider is certified by the alpha-7 local fixture suite. Compatibility
belongs to an exact API prefix, upstream protocol, model, feature and policy.
An HTTP 200, a model catalog entry or a passing native request is not enough.

## Before a live run

- Obtain the operator's approved provider/model list, key reference, allowed
  request count and possible cost. Never put a key in a report or command line.
- Check current provider documentation for endpoint/model availability. Do not
  infer a Responses/Messages endpoint from Chat support or guess URL prefixes.
- Use synthetic prompts, disposable CLI state, no real repository files and
  no change to installed launchers, PATH, defaults or real sessions.
- Do not reinstate the previously excluded UnoRouter Gemini/Qwen, Inkling or
  GPT-OSS probes without a new request. Respect quotas and access failures.
- Keep retries off for certification unless the test explicitly covers approved
  bounded retry behavior. No key rotation, model fallback, paid substitution or
  rapid retry loops to turn a failure into a pass.

## Record each scenario

| Field | Required evidence |
| --- | --- |
| Identity | Exact prefix, protocol, model ID, time, CLI/core version and commit |
| Policy | Reasoning override, public-reasoning flag, usage/search/schema policy |
| JSON/SSE text | Exact synthetic answer and valid completion at upstream EOF |
| Function tools | Arguments, unique call/result identities and post-tool answer |
| Client search | Search output schemas, authorized tool execution and replay |
| Resume | Same temporary session, synthetic recall and retained tool history |
| Constraints | Supported schema/output limits, refusals and error classifications |
| Isolation | Concurrent model/provider sessions cannot exchange keys or history |
| Outcome | Pass, explicit unsupported, provider failure, timeout or unverified |

Check native controls and every translated direction claimed for that feature;
never extrapolate all six routes from one. Signed/encrypted/hidden reasoning
cannot be certified as portable by flattening it into text or fabricating state.
Intentional strict rejections are not adapter bugs or passing feature support.

Existing scripts/probe_clients.py, probe_formats.py and related scripts provide
opt-in live probes. Review their options and current key/provider authorization
before execution; defaults from old experiments are not universal presets.
Record shape-only diagnostics and sanitized classifications, not raw credentials
or private messages. ProviderProfiles token-field aliases require independent
documentation AND evidence of equivalent completion-total budget semantics.

Only then update exact-scope capability/profile records and compatibility docs.
Remote CI, local fixtures, live provider support and publication are four
different claims; keep their evidence separate.
