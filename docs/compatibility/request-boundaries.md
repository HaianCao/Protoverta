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
