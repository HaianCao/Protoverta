# Getting started

Protoverta needs Python 3.11 or newer and has no dependencies.

## Install

```console
python -m pip install protoverta
```

Each [GitHub release](https://github.com/HaianCao/Protoverta/releases) also carries the wheel, the source distribution and a `SHA256SUMS` file; builds are attested with GitHub's build provenance.

## Convert a request

```python
from protoverta import convert_request, convert_response

messages_request = {
    "model": "your-model-id",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Hello"}],
}

chat_request = convert_request(messages_request, "messages", "chat")
# ... send chat_request to a Chat Completions provider, then:
# messages_response = convert_response(chat_response, "chat", "messages")
```

Anything that cannot be converted faithfully raises {class}`~protoverta.ConversionError` (or {class}`~protoverta.UnsupportedFeatureError` for a recognised but unsupported feature). See [Using the library](library.md) for streams.

## Run the gateway

```powershell
$env:PROTOVERTA_UPSTREAM_KEY = "your-provider-key"
$env:PROTOVERTA_LOCAL_TOKEN = "a-long-random-local-token"
protoverta --upstream-protocol chat --upstream-base-url https://provider.example/v1 `
  --upstream-key-env PROTOVERTA_UPSTREAM_KEY --inbound-token-env PROTOVERTA_LOCAL_TOKEN --port 8765
```

The gateway listens on `127.0.0.1:8765` and prints `READY http://127.0.0.1:8765`. Point a client at it with the local token as its API key: a Messages client (`POST /v1/messages`) now talks to a Chat Completions provider. See [Running the gateway](gateway.md).
