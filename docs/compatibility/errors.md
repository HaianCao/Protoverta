# Gateway failures and resource boundaries

Translated and locally generated errors use one numeric-status classification:
invalid request, authentication, permission, not found, too large, rate limit,
overload, timeout or generic upstream failure. Recognized structured context
codes map to `context_length_exceeded`; arbitrary message text does not authorize
context classification or request changes. Unknown provider codes are not copied
into the normalized code vocabulary.

Messages uses a `type: error` envelope and Messages error types. Chat/Responses
use an `error` object, including `server_error` for generic upstream failures.
Responses SSE errors instead use the typed `error` event's top-level `code`,
`message`, `param` and monotonically increasing `sequence_number`, not the HTTP
error envelope.
HTTP statuses and allowed retry/request-ID headers are preserved. Native upstream
API errors keep their original valid JSON body and status. This normalization
does not add retries, provider/model fallback or output repair.

Translated HTTP error messages are bounded and redact known configured keys and
tokens. Raw transport/stream exception text is not exposed. Native error bodies
remain upstream-owned and are not rewritten. Keep raw diagnostics disabled and
never use private conversations as fixtures.

Unsupported reasoning before visible content returns HTTP 422 with
`unsupported_reasoning` and `x-should-retry: false`. Stream errors after content
produce an SSE error, never a successful terminal event. Socket timeouts map to
504; malformed/truncated upstream streams remain 502. No estimated token usage
is substituted to make an interrupted stream appear successful.

`--max-concurrent-requests` (Python: `max_concurrent_requests`) defaults to 16
active authenticated requests per gateway. Excess requests receive 503 with
`Retry-After: 1` without reaching upstream. Slots are released on completion,
failure or disconnect. This is local concurrency control, not a provider RPM/TPM
rate limiter and not a global limit across gateway processes.

`--timeout` (Python: `timeout`) defaults to 120 seconds and bounds socket I/O,
including client body reads. It is **not a total generation or semantic-progress
deadline**. Incomplete client body reads time out with 408. Limits must be finite
and positive; no timeout, sandbox, privilege or real user installation is altered
by the test harnesses.

Separate upstream budgets are now available: `--total-timeout` (600 seconds)
and `--progress-timeout` (120 seconds). The former never resets; the latter
resets for content/tool-argument progress, not pings, usage or repeated function
metadata. Deadlines cover header/body reads in the default HTTP(S) transport,
including slow-drip headers/JSON bodies, HTTP chunk-size lines, chunk terminators
and trailers. OS-rounded deadline timeouts retain non-retryable deadline codes;
a shorter socket I/O timeout keeps its transport classification. Connection establishment uses the
smaller remaining socket budget; OS DNS resolution is not forcibly cancellable.
Custom injected transports must implement bounded I/O themselves; checks around
their reads cannot interrupt arbitrary blocking Python callbacks.

`--max-stream-bytes` (32 MiB), `--max-stream-items` (1024), and
`--max-tool-argument-bytes` (8 MiB) bound cumulative upstream wire data, logical
items, and aggregate argument data before successful completion. These bounds
also apply to native `StreamValidator`; library `StreamTranscoder` accepts the
same Python options. Low-level direct decoders remain caller-managed.
The budget limits retained data asymptotically, not an exact process-RSS ceiling.
Oversized streams fail with `stream_limit`; explicit elapsed budgets use
`request_deadline` or `progress_timeout`. No success terminal is fabricated.
Local policy-limit failures are non-retryable before headers are sent.

Malformed enum types produce structured validation errors (422 for client
requests, 502 for upstream output). JSON recursion failures and truncated HTTP
bodies also fail explicitly; partial HTTP payload bytes are not copied into
generated error messages.

Reference: [OpenAI Responses error events](https://developers.openai.com/api/reference/resources/responses/streaming-events).
