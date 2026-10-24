# UnoRouter observations

## Active scope decision (2026-10-24)

The user reports that Gemini's failure is provider-side and has requested
dropping both `gemini-3.6-flash:free` and `qwen3.6-35b-a3b:free` from further
testing. No more live tool/resume probes are scheduled for these models, and
their unresolved results are not release gates. The provider-side attribution
is user-reported rather than independently established by the recorded probes.
The observations below remain historical evidence, not tool-support claims.
Generic diagnostics and protocol regression tests remain applicable; no
credentials, provider configuration or generic protocol support are removed.

## Historical test results

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

## Follow-up diagnosis

The source launchers again passed keyless Chat tool fixtures with the actual
Codex and Claude CLIs, including matching call/result IDs, final answers and
clean upstream EOF. These scripted streams do not establish UnoRouter tool
support. A negative fixture also reproduces an encoder failure with **no decoder
exception** when a tool stream lacks final usage. That is a diagnostic regression
case, not an established cause of Gemini's live failure.

Fresh `scripts/probe_clients.py` probes now accept `--diagnostics` and
`--timeout SECONDS`. Diagnostics record bounded Chat frame shapes, stage/EOF
counters, HTTP status and fixed failure categories across transport, framing,
decoding, Messages ordering and encoding. They exclude raw client output,
payloads, unknown field names, call IDs, arguments, reasoning and credentials.
The original exceptions and strict conversion rules remain in force.

The isolated Windows process runner now assigns a suspended probe to a
kill-on-close job before allowing it to execute. Timeout cleanup terminates
that job, including descendants when the parent already exited, before draining
inherited output pipes. It does not request job breakaway or change privileges.
The earlier taskkill-based runner failed this cleanup check in the receiving
environment; a containment failure now stops the child before execution.

`--diagnostics --max-upstream-requests 1` limits a first-response diagnosis to
one upstream attempt. A later tool-result request stops locally with the
`probe_request_budget` category, distinct from an upstream 429. Such a probe
cannot count as a passing completed tool loop. The budget does not check quota
or schedule requests around the provider's rate limit.

UnoRouter documents one request per minute per free model/user, plus upstream
and daily pool limits. Check the existing key's quota with its read-only
`/api/usage/token/` endpoint and allowed models with `/v1/models` before a live
generation; honor Retry-After/reset and space tool continuations accordingly.
In this follow-up environment the quota check was blocked by a socket permission
error (Windows 10013), so **no live generation was attempted**. Gemini's root
cause, Qwen's current quota, and both models' live tool/resume support remain
unverified. No decoder/encoder compatibility mapping was changed on this evidence.

References:

- [UnoRouter's Claude integration guide](https://unorouter.com/en/docs/integrations/claude-code)
- [Rate limits and errors](https://unorouter.com/en/docs/platform/errors-and-rate-limits)
- [Key usage and model listing](https://unorouter.com/en/docs/platform/platform-api)
