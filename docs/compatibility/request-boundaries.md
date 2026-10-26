# Request boundaries and error contracts

The gateway binds to loopback; it is not a public reverse proxy. Its production
`GatewayHTTPServer` bounds accepted handler threads with `max_concurrent_requests`,
including clients that have not sent headers or authenticated. Excess connections
are closed before HTTP parsing; a structured 503 is not guaranteed at this stage.
Embedders using `make_handler` with another server own connection admission.

Each handler creates one absolute `total_timeout` budget before HTTP parsing.
Raw socket reads enforce that same budget beneath buffering, so a trickling header
or body cannot reset the total deadline. Socket I/O and progress limits also apply.
The remaining budget is reused by upstream generation or native token counting.
Malformed framing (duplicate Content-Length or any Transfer-Encoding) is rejected;
chunked uploads are unsupported. The server closes each HTTP/1.0 request connection.
Header parse timeouts may simply close the connection; authenticated body timeouts
return 408 when the client can still receive it. These are socket/network deadlines,
not preemption of trusted operator callbacks, JSON CPU work or arbitrary filesystems.

JSON boundaries require finite numbers and valid Unicode scalar values, including
values inside schemas and object keys. NaN/Infinity, exponent overflow, unpaired
surrogates, and numbers/nesting exceeding the Python parser's supported limits
produce a structured error, never character replacement or numeric clamping.
Locally invalid requests return nonretryable 422 before upstream dispatch. Invalid
upstream JSON returns 502; malformed SSE produces a stream failure and no successful
terminal event. The same grammar checks apply to native HTTP forwarding; valid
native feature fields are still preserved without cross-protocol interpretation.
Translated library requests/responses apply these checks too. Same-protocol library
copy helpers remain passthrough helpers; they are not standalone JSON validators.
Validation does not promise arbitrary-precision decimal arithmetic or change the
Python interpreter's integer-size protection.

Count endpoints distinguish local validation/conversion failure (422), provider
HTTP failures (their sanitized 4xx/5xx status), provider transport/schema failure
(502), and provider timeout (504). Allowed retry/rate headers use the same allowlist
as generation responses. Provider bodies/reason phrases are not copied by counting.
429/503 Retry-After updates shared admission cooldown when enabled. No generation,
heuristic estimate or alternate provider is used as a fallback.

Doctor records `incomplete`, with `passed=false`, when JSON or SSE reports a length
limit, even if the synthetic nonce arrived. Failed/truncated checks export no
positive capability evidence. Protocol conversion itself still faithfully maps
incomplete output; certification demands successful completion of its probe.
Tool probes also reject completed streams whose stop reason contradicts the
presence of tool calls. Chat `stop` / Messages `end_turn` must not certify a
tool-use result; length-limited output remains incomplete evidence.

Translated request history requires nonempty tool call/result identities and at
most one result per call. Opaque IDs are not normalized; parallel results may
arrive in a different order from their calls. Native passthrough is unchanged.
For Messages SSE, an explicit initial `input: {}` followed by no argument tokens
is preserved as `{}` at block closure. Missing/non-object input, whitespace-only
or malformed argument JSON is rejected; empty Chat argument strings are not
repaired. Chat/Responses targets reject nonempty text after a tool call in both
JSON and SSE to preserve the existing strict ordering subset. Empty text deltas
carry no content and do not create post-tool items. Chat SSE choice indices must be
exact integer zero, including repeated-choice usage postludes.

Translated requests involving Messages require temperature in 0–1; Chat ↔
Responses retains 0–2. Model/API-version restrictions may be narrower and are
not inferred, rescaled or silently removed. Native passthrough is unchanged.
Chat measured usage is accepted once on a finishing choice, an empty-choice tail,
or the supported repeated-choice postlude. Duplicate/conflicting reports fail;
nonnull usage before a finishing choice is explicitly unsupported. Null usage
placeholders do not prevent later measurements.

Doctor uses the configured public-reasoning policy when decoding both JSON and
SSE probe results. Enabling unsigned Chat/Responses reasoning does not certify a
reasoning feature, accept opaque/encrypted state or relax the Messages signature
contract. Configured gateway inbound tokens must be ASCII; malformed non-ASCII
bearer headers return 401 instead of raising a comparison error.

## Launcher setup listeners

CodexApter and ClaudApter use their own standard-library-only setup transport;
direct setup/imports do not require Protoverta. Each setup listener permits at
most 16 accepted handler connections, including unauthenticated clients. Excess
connections close before parsing, without promising a structured HTTP response.
The absolute header deadline is 10 seconds; body input gets at most 15 seconds
after headers, within a 30-second input budget. Raw reads enforce the deadline
beneath buffering, so trickling bytes cannot renew it. Response writes have a
two-second socket timeout. These bound client I/O, not trusted callbacks or disk
work. Closing the listener also shuts down pending client sockets.

Both setup apps reject malformed non-ASCII query/header tokens with 403 and retain
their existing host/origin checks. A body-read timeout returns 408 when the client
can still receive it; header/early disconnects can simply close. The web boundary
tests use short test-only limits and a synthetic configuration, with recovery and
Run shutdown checked separately in real Chromium.
