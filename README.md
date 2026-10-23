<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/protoverta_logo_black.png">
  <img src="assets/protoverta_logo_white.png" alt="Protoverta logo" width="180">
</picture>

# Protoverta

Universal LLM Protocol Adapter

Chat Completions <-> Messages <-> Responses

One Gateway. Any Protocol. Any Model.

</div>

Protoverta converts requests and responses among OpenAI Chat Completions, Anthropic Messages, and OpenAI Responses. Use it as a Python library or run it as a local HTTP gateway.

**Current scope:** text and client-side function tools, including incremental SSE where usage and event ordering can be preserved. Unsupported fields and unsafe streaming routes are rejected rather than silently changed. Multimodal content, token counting, and server-side conversation state are not supported.

## Install

Python 3.11+ is required.

```powershell
python -m pip install -e ".[test]"
```

## Library

```python
from protoverta import convert_request, convert_response

messages_request = {
    "model": "your-model-id",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Hello"}],
}

chat_request = convert_request(messages_request, "messages", "chat")
# messages_response = convert_response(chat_response, "chat", "messages")
```

`ConversionError` indicates an unsupported or invalid JSON conversion. For SSE, `StreamTranscoder` accepts upstream bytes via `feed()` and requires `finish()` at EOF; it raises `StreamError` on invalid or interrupted streams. Protocol names are `chat`, `messages`, and `responses`.

## Gateway

Provide credentials through environment variables:

```powershell
$env:PROTOVERTA_UPSTREAM_KEY = "your-upstream-key"
$env:PROTOVERTA_LOCAL_TOKEN = "a-long-random-local-token"
python -m protoverta --upstream-protocol chat --upstream-base-url https://provider.example/v1 --upstream-key-env PROTOVERTA_UPSTREAM_KEY --inbound-token-env PROTOVERTA_LOCAL_TOKEN --port 8765
```

The gateway accepts `POST /v1/chat/completions`, `POST /v1/messages`, and `POST /v1/responses`. It binds to `127.0.0.1`, prints its URL on startup, and requires `Authorization: Bearer <local-token>`. Use `--auth-style x-api-key` for upstreams that require that header.

Streaming supports all six directions for text and client-side function calls. Chat/Responses → Messages requires the explicit `--messages-usage-policy deferred` option (or `StreamTranscoder(..., messages_usage_policy="deferred")`). Initial usage is provisionally zero, then corrected with measured usage in `message_delta`; final input and output counts are mandatory. Overlapping tool calls are serialized in first-start order with bounded buffering. The default `strict` policy still rejects these two routes.

Native streams preserve feature payloads, including thinking/signatures, while validating framing and lifecycle. Successful terminal events are held until upstream EOF is verified. Cross-protocol conversion rejects unsupported content, annotations, logprobs and interrupted streams. The upstream base URL is the exact API prefix: include `/v1` or another version when required; Protoverta only appends the endpoint suffix. Native upstream API errors and allowed retry headers are preserved.

Usage normalizes total input tokens, including Messages cache reads and writes, and preserves reported cache/reasoning subtotals where the target can represent them. Generated Responses usage includes zero-valued cache/reasoning subtotals only when the source does not provide them; these are compatibility defaults, not measured values. Interim deferred usage is not accurate context accounting. Effort levels `low`, `medium` and `high` map between each API's effort option; models determine their effect and availability.

For Codex's stateless text/function subset, opt in with `--client-profile codex`. This policy omits client telemetry, cache hints and the encrypted-reasoning include hint. It requires reasoning summaries to be disabled, maps initial developer instructions to Messages system text, and accepts an explicit Messages fallback limit via `--default-max-output-tokens`. Namespaced/hosted tools and hidden reasoning replay remain unsupported. The default `strict` profile does not perform these client-specific adjustments.

For Claude Code translation, use `--client-profile claude --messages-usage-policy deferred`. Disable thinking, experimental betas, structured output and tool search in the client before sending requests. The profile omits cache hints and client metadata, accepts the SDK's `beta=true` query marker without forwarding it to another protocol, and represents tool-result errors as explicitly labelled text. Thinking/signature blocks, compaction and hosted tools remain unsupported. Native Messages does not apply this profile's rewriting. See [the streaming policy decision](docs/decisions/ADR-001-messages-streaming-rejection.md).

## Contributing

PRs should include sanitized fixtures and tests for both directions of any protocol change. Never commit API keys or private conversations.

```powershell
python -m pytest
ruff check src tests
```

Installed CLI clients can be exercised without provider keys. The smoke harness uses a loopback fixture, temporary client state and two read-only tool calls:

```powershell
python scripts/smoke_clients.py --client codex --upstream chat --tool-loop
python scripts/smoke_clients.py --client claude --upstream messages --tool-loop
python scripts/smoke_clients.py --client claude --upstream chat --tool-loop
python scripts/smoke_clients.py --client claude --upstream responses --tool-loop
```

Use `--adapter-source PATH` to include a source launcher, or `--installed-adapter` to test a launcher installed in the active Python environment. These fixtures verify client/protocol interoperability, not a real provider's model capabilities.

See [SECURITY.md](SECURITY.md) for reporting security issues. Licensed under [MIT](LICENSE).
