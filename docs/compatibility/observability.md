# Payload-free local diagnostics

Diagnostics are opt-in. The standalone gateway accepts `--diagnostics` and
writes one JSON line to stderr for each dispatched POST when its handler finishes.
It does not create files or retain a global request history. The normal READY
message remains on stdout. Without that flag the gateway does not log request
records. Embedded callers can pass `observer=` to `make_handler`, `serve` or
`gateway_session`. Launcher previews now expose an opt-in per-session journal
directory and last-session summary; see [setup diagnostics](advanced-adapters.md).

## Correlation and schema

Every dispatched POST gets a new random 32-hex-character local ID. Responses
include `X-Protoverta-Request-ID`, whether diagnostics are enabled or not. Client
IDs and upstream IDs are never reused as the local ID or forwarded upstream as
this header. Existing selected upstream response headers remain governed by
the separate response-header allowlist; that does not put them in diagnostics.

Example shape (synthetic values):

```json
{"request_id":"0123456789abcdef0123456789abcdef","protocol":"responses","upstream_protocol":"chat","status":200,"outcome":"invalid_stream","duration_ms":18,"streaming":true}
```

Fields are fixed: request ID, recognized inbound/upstream protocol, HTTP status
or null, fixed outcome category, nonnegative elapsed milliseconds and streaming
boolean, optional first-payload milliseconds and measured input/output/cache
read/write counts. Missing measurements remain null, not zero. First payload is
not model TTFT. Unknown endpoint paths become `protocol="unknown"`; raw paths/query
strings, base URLs, provider/model names, keys, headers, messages, tool schemas,
arguments, outputs, reasoning and exception text are not recorded. These local
records contain no provider trace ID and cannot by themselves identify which
Azure resource issued an opaque item.

`status` is the selected HTTP status, not proof of delivery or successful
generation. A stream may commit HTTP 200 and subsequently fail validation;
its outcome is then `invalid_stream`, `unsupported_reasoning`, `stream_limit`
or `timeout`, not `success`. No successful terminal event is fabricated.
Client write failures/cancellation are distinct from upstream stream resets.
Fixed categories also distinguish local capacity, shared admission exhaustion,
unavailable admission storage, local request rejection and upstream HTTP/transport
failure. Native upstream error bodies are still passed through unchanged under
the existing native policy; telemetry never copies those bodies.

## Boundaries

- Exactly one record is attempted per dispatched POST, not one per retry attempt.
  Malformed HTTP rejected by the standard-library parser, GETs and process crashes
  before handler completion are outside this contract.
- Records are immutable, with no unbounded labels/content. There is no built-in
  metrics endpoint, delivery guarantee, provider tracing, log rotation or retention
  service. Operators control any stderr redirection/retention and its permissions.
- The built-in JSONL sink serializes writes. Embedded callbacks are synchronous;
  they must be bounded, thread-safe and nonblocking. A blocked sink can delay
  request-thread cleanup; no unbounded background queue is created to hide that.
  This refers to `jsonl_observer`/custom callbacks. The optional launcher session
  journal instead uses a bounded 128-record queue and a daemon file writer, with
  dropped-record reporting when it can finish. See the setup diagnostics limits.
- Ordinary observer exceptions are suppressed without logging their message,
  retrying upstream or changing the response. Process interrupts are not swallowed.
- The callback receives records only, not config/credentials. Custom observers
  are trusted application code and can introduce their own retention/privacy risks.
- No API schema, reasoning compatibility, provider routing or installed session
  state is changed by enabling diagnostics. This does not solve cross-resource
  replay of server-owned IDs or encrypted reasoning.
