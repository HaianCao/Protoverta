# UnoRouter observations

Checked on 2026-10-23 with synthetic prompts, isolated CLI state and the source
integration launchers. API prefix: `https://api.unorouter.com/v1`; Chat endpoint:
`/chat/completions`. Credentials stayed in a local environment variable and
were not written to fixtures or reports.

| Model | Direct Chat JSON | Codex via Chat to Responses | Claude via Chat to Messages |
| --- | --- | --- | --- |
| `gemini-3.6-flash:free` | Passed | Text passed, provider-default reasoning | Text passed, provider-default reasoning |
| `qwen3.6-35b-a3b:free` | Passed | Text passed, explicit reasoning disabled | Text passed, explicit reasoning disabled |

Qwen's default completed Chat response included a nonempty `reasoning` field.
This is not the opt-in `reasoning_content` extension. The translated CLI probes
used `upstream_reasoning="disabled"`, which sends `reasoning_effort: "none"` to
Chat. This is a request override, not a filter that discards returned reasoning.
Default reasoning conversion remains unverified and unsupported fields still
fail under strict policy. A separate Qwen native Messages text probe with
thinking explicitly disabled also passed; native forwarding is not a conversion
test.

Tool loops are **not verified** for either model. A Gemini Codex `get_goal`
attempt failed with a gateway 502 stream error before a matching tool result;
a later controlled diagnostic also failed without verifying a tool. A Qwen
attempt received upstream HTTP 429. No paid fallback, app-identity substitution
or quota workaround was attempted. These failures do not establish that the
provider or model lacks tool support; they do prevent claiming a passing
integration. Cross-provider resume with these two targets has not been tested.

These are short observations of specific routes, not a six-direction
conformance result or a guarantee about subsequent turns. No user installation,
PATH, default configuration or existing session was changed.

Reference: [UnoRouter's Claude integration guide](https://unorouter.com/en/docs/integrations/claude-code).
