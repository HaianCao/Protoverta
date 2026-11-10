# HTTP API

The gateway serves the endpoints of all three protocols on `http://127.0.0.1:<port>`. Each request is converted to the configured upstream protocol, or forwarded unchanged when the protocols match.

## Endpoints

| Method and path | Protocol | Notes |
|---|---|---|
| `POST /v1/chat/completions` | Chat Completions | JSON or `stream: true` (SSE) |
| `POST /v1/messages` | Anthropic Messages | JSON or SSE; the `?beta=true` query of the Anthropic SDK is accepted with `--client-profile claude` |
| `POST /v1/messages/count_tokens` | Anthropic Messages | Needs `--token-count-mode` (`estimate` or `native`) |
| `POST /v1/responses` | Responses | JSON or SSE |
| `POST /v1/responses/input_tokens` | Responses | Needs `--token-count-mode` |
| `POST /v1/responses/compact` | Responses | Native Responses upstream only, not streamed |
| `GET /v1/models` | Messages or OpenAI list | Serves the session's model snapshot (set by the launchers); `anthropic-version` selects the Messages format |

## Authentication

Send the inbound token as `Authorization: Bearer <token>`. `GET /v1/models` also accepts `x-api-key`, alone or together with `Authorization`, as long as every credential sent matches. Requests without a valid token get `401`.

## Request headers

`Content-Length` is required; chunked or ambiguous framing is rejected (`400`/`411`). Bodies are limited to 8 MiB by default. Request headers must arrive within 15 seconds. On Messages routes `anthropic-version` and `anthropic-beta` are forwarded unchanged to a Messages upstream. `X-Protoverta-Agent-ID` and `X-Protoverta-Chain-ID` are hashed into diagnostics and never forwarded.

## Response headers

| Header | Meaning |
|---|---|
| `X-Protoverta-Request-ID` | A fresh ID for this request, also in diagnostics |
| `X-Protoverta-Finish-Category: truncated_output` | The output stopped at the token limit |
| `X-Protoverta-Count-Kind`, `X-Protoverta-Count-Category` | How a token count was produced (`provider-native`, `operator-exact`, estimate) |
| `X-Protoverta-Context-*` | The context preflight verdict and counts, when enabled |
| `retry-after`, `x-should-retry`, `request-id`, `x-request-id`, `anthropic-ratelimit-*` | Forwarded from the provider |

## Errors

Errors use the envelope of the client's protocol. Messages clients receive `{"type": "error", "error": {...}}`; Chat Completions and Responses clients receive `{"error": {...}}`. The error object carries:

- `type`: the protocol's error type (for example `invalid_request_error`, `authentication_error`, `rate_limit_error`, `overloaded_error`, `api_error`);
- `message`: a readable message with known secrets removed;
- `code`: a stable Protoverta code (below) or the error kind;
- `phase`: where it failed (`request`, `preflight`, `admission`, `upstream`, `response`, `stream`), and `feature`/`path` when relevant.

| Code | Status | Meaning |
|---|---|---|
| `unsupported_feature` | 422 | A recognised feature cannot be carried to the upstream protocol |
| `unsupported_reasoning` | 422 | Reasoning state the route cannot preserve |
| `policy_refusal` | 422 | A refusal or content filter the route cannot represent |
| `invalid_stream` / `invalid_response` | 502 | The provider sent an invalid stream or response |
| `tool_policy_violation` | 502 | The provider called a tool the request did not allow |
| `stream_limit` | 502 | A stream exceeded a configured size limit |
| `upstream_transport` / `upstream_timeout` | 502 / 504 | The provider could not be reached or timed out |
| `request_deadline` / `progress_timeout` | 504 | The request's total or progress deadline passed |
| `internal_error` | 500 | An unexpected gateway fault; nothing was retried |
| `context_length_exceeded` | 400 | The provider reported a context overflow |

Errors that will fail again if resent carry `x-should-retry: false`. Native routes return the provider's own error body unchanged. Errors inside an SSE stream after the first event are sent as the protocol's `error` event. See [errors and resource limits](compatibility/errors.md) for the full mapping.
