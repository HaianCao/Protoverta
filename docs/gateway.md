# Running the gateway

```console
protoverta --upstream-protocol {chat,messages,responses} --upstream-base-url URL \
  --upstream-key-env VAR --inbound-token-env VAR [options]
```

`python -m protoverta` is equivalent. Both secrets come from environment variables so they never appear in the process list or shell history.

- **Upstream:** `--upstream-protocol` is the API your provider speaks. `--upstream-base-url` is its exact API prefix, including `/v1` (or another version) when the provider needs it; Protoverta only appends `/chat/completions`, `/messages` or `/responses`. HTTPS is required except for loopback addresses. Use `--auth-style x-api-key` for providers that want that header instead of `Authorization: Bearer`.
- **Local clients** send `Authorization: Bearer <inbound token>` to the endpoint of their own protocol. The gateway binds to `127.0.0.1` only. See [HTTP API](http-api.md).
- **Profiles:** `--client-profile codex` and `--client-profile claude` apply the documented compatibility policies of Codex CLI and Claude Code. The default `strict` profile performs no client-specific changes.
- **Reasoning:** `--upstream-reasoning` (`provider_default`, `disabled`, `openrouter_disabled`), `--reasoning-output omit` and `--public-reasoning` control reasoning explicitly. See [reasoning](compatibility/reasoning.md).
- **Limits:** `--max-concurrent-requests` (16), `--timeout` (300 s socket I/O), `--progress-timeout` (300 s without progress), `--total-timeout` (3600 s per request), and stream size limits. See [errors and resource limits](compatibility/errors.md).
- **Retries:** `--retry-policy` resends requests that failed with transient errors. See [Retries](retries.md).
- **Diagnostics:** `--diagnostics` writes one payload-free JSON line per request to stderr. See [observability](compatibility/observability.md).

Every option is listed in the [command reference](cli.md). The same settings are fields of {class}`protoverta.server.GatewayConfig`; from Python, {func}`protoverta.server.gateway_session` runs a gateway for the duration of a `with` block:

```python
from protoverta.server import GatewayConfig, gateway_session

config = GatewayConfig("chat", "https://provider.example/v1", provider_key, local_token, client_profile="claude", messages_usage_policy="deferred")
with gateway_session(config) as url:   # "http://127.0.0.1:<port>"
    run_client(base_url=url, api_key=local_token)
```
