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

`reasoning`, OpenRouter `reasoning_details`, nonempty Responses `summary`,
encrypted content, signatures, redacted thinking and unknown fields are still
errors. A summary is not raw reasoning; an encrypted blob is not text.

Messages requires its own thinking/signature contract. Unsigned text is not
mapped to Messages thinking, given a fake signature, or inserted into ordinary
assistant text. A provider returning such output on a translated Messages route
can therefore still fail. The successful text-only CLI probes do not remove
that boundary.

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
