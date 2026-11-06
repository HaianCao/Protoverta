# Reasoning: transport is not model state

By default translated routes reject nonempty reasoning output or replay items.
Native routes retain their original payloads; they do not establish that another
model/account/provider can read those payloads.

## Unsigned public text (opt-in)

`public_reasoning=True` on `convert_request`, `convert_response`,
`StreamDecoder`, `StreamEncoder`, `StreamTranscoder` and `GatewayConfig` enables
one narrow extension. The gateway CLI flag is `--public-reasoning`.

| Chat-compatible extension | Responses |
| --- | --- |
| Assistant `reasoning_content: string` | `type: reasoning`, empty `summary`, one `content` part of `type: reasoning_text` |

This is **not** standard OpenAI Chat reasoning. The selected Chat provider must
accept/return that extension. It is used by several NVIDIA NIM models. It keeps
public reasoning distinct from the assistant's answer, including on completed
JSON, incremental SSE and subsequent explicit request-history conversion.
It does not enable model computation, select a reasoning budget, or expose
anything the upstream did not already return publicly.

Only a prefix before assistant text/function calls is supported. Interleaved
reasoning is rejected, not relocated. SSE validates item identity, part lifecycle,
completed text and final output snapshots. Truncated streams cannot produce a
successful terminal event. The strict default is unchanged.

OpenRouter streams the same unsigned text as `reasoning`; it is treated like
`reasoning_content` (never both in one delta). OpenRouter `reasoning_details`,
nonempty Responses `summary`, encrypted content, signatures, redacted thinking
and unknown fields are still errors unless omitted by the policy below. A
summary is not raw reasoning; an encrypted blob is not text.

## Omitting unrepresentable reasoning (opt-in)

`reasoning_output="omit"` (`--reasoning-output omit`; `omit_reasoning=True` on
`convert_response`, `StreamDecoder` and `StreamTranscoder`) drops upstream
reasoning that the client's protocol cannot represent on a **translated** route,
instead of failing the request: Chat `reasoning`/`reasoning_content`/
`reasoning_details`, Messages `thinking`/`redacted_thinking` blocks (with their
signature deltas) and Responses `reasoning` items (with their summary/text
events). Answer text, tool calls, usage and stop reasons are unchanged. Nothing
is fabricated, re-signed, summarized or forwarded to another provider. Native
routes are untouched. Successful streams record diagnostics feature
`reasoning_omitted`. The default remains `reject`.

Why it exists (live checks, 2026-11-06, Claude-style Messages tool loop over
Chat providers): a disable request is not portable. `stealth/crouton-alpha`
(OpenRouter) answers `reasoning_effort: none` with HTTP 400 "Reasoning is
mandatory"; `minimaxai/minimax-m3.1-flash` and `z-ai/glm-5.3-flash` (NVIDIA NIM)
ignore it and still stream `reasoning_content`; `deepseek-ai/deepseek-v4.1-flash`
honours it. Under `reject` all five tested models failed the first turn; under
`omit` all five completed a two-turn tool loop, including models that reason on
every turn. The launchers' `auto` policy now leaves the provider default in
place and selects `omit` on translated routes (plus unsigned public reasoning
between Chat and Responses). Choose an explicit `disabled` policy to request
less reasoning compute where a model honours it.

## Provider errors inside a stream

Some servers return HTTP 200 and then an error object as an SSE event (NVIDIA
NIM `deepseek-ai/deepseek-v4.1-flash` does this for `reasoning_effort: medium`
while accepting `low`/`high`). Translated routes surface the provider's bounded,
secret-redacted message ("upstream provider reported an error: ..."), using the
provider's 4xx status and its standard error code before any output (with
`x-should-retry: false`, except 408/409/429) or 502 otherwise, instead of a
generic `invalid_stream`. Diagnostics record outcome `upstream_http`. Declare a model's allowed
efforts in a version-2 capability record to reject such requests before I/O.

Messages requires its own thinking/signature contract. Unsigned text is not
mapped to Messages thinking, given a fake signature, or inserted into ordinary
assistant text. A provider returning such output on a translated Messages route
can therefore still fail. The successful text-only CLI probes do not remove
that boundary.

For translated SSE, unsupported reasoning detected before any answer/tool
payload returns HTTP 422 with `code: unsupported_reasoning` and
`x-should-retry: false`. Initial metadata-only events are held in a bounded
buffer so an empty stream start does not prematurely commit HTTP 200. If
answer/tool output has already been sent, the stream ends with an error event,
never a fabricated successful terminal event. Malformed or truncated upstream
streams remain upstream errors, not reasoning-policy errors.

CodexApter's integration branch exposes the option only for a Chat gateway
provider. In TOML it is `public_reasoning = true`; setup has an advanced checkbox.
ClaudApter does not expose it because its downstream protocol is Messages.
Provider capability registries recognize `public_reasoning` independently of
`reasoning_effort`.

## Opaque/signed reasoning

Full cross-vendor reasoning-state migration is not implemented. Anthropic's
current preserved-thinking contract may bind blocks to the model, account and
unchanged preceding context. Re-encoding a signed block, changing its prefix,
or replacing it with a public summary is not a safe universal conversion.
OpenAI encrypted reasoning is also server/model-owned state, not a portable
plaintext serialization.

A future vendor-specific bridge would need an explicit compatible format,
matching model/account binding, exact unmodified replay, complete tool-loop and
streaming fixtures, and live evidence. Supporting a new JSON field alone is not
sufficient. No hidden-state fallback or lossy text projection is performed.

References:

- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
- [OpenAI reasoning SSE events](https://developers.openai.com/api/reference/resources/responses/streaming-events)
- [Anthropic tool/thinking workflows](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows)
- [Anthropic preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking)
- [OpenRouter reasoning formats](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
