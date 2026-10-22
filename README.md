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

The gateway accepts `POST /v1/chat/completions`, `POST /v1/messages`, and `POST /v1/responses`. It binds to `127.0.0.1`, prints its URL on startup, and requires `Authorization: Bearer <local-token>`. Use `--auth-style x-api-key` for upstreams that require that header. Streaming conversion is incremental; Chat/Responses → Messages is rejected because those sources do not report input-token usage at stream start. Other missing usage or unsupported interleaving fails explicitly instead of emitting a false completion.

## Contributing

PRs should include sanitized fixtures and tests for both directions of any protocol change. Never commit API keys or private conversations.

```powershell
python -m pytest
ruff check src tests
```

See [SECURITY.md](SECURITY.md) for reporting security issues. Licensed under [MIT](LICENSE).
