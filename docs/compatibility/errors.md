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
When no invalid request parameter is identified, translated Responses SSE
errors use `param: null`. The optional gateway `path` names the HTTP route and
is separate from `param`; clients must not interpret a route as a field name.
HTTP statuses and allowed retry/request-ID headers are preserved. Native upstream
API errors keep their original valid JSON body and status. This normalization
does not add retries, provider/model fallback or output repair.

Translated HTTP error messages are bounded and redact known configured keys and
tokens. Raw transport/stream exception text is not exposed. Native error bodies
remain upstream-owned and are not rewritten. Keep raw diagnostics disabled and
never use private conversations as fixtures.

Translated tool-policy violations use `tool_policy_violation` and
`feature=tool_policy`. Before headers they return 502 with
`x-should-retry: false`; after visible SSE they emit an error without a success
terminal. The gateway does not replay generation or execute the offending tool.
Errors use fixed messages without tool names or arguments. Unsatisfiable client
policies are rejected before upstream I/O with 422. See
[effective request metadata](response-metadata.md).

Unsupported reasoning before visible content returns HTTP 422 with
`unsupported_reasoning` and `x-should-retry: false`. Stream errors after content
produce an SSE error, never a successful terminal event. Socket timeouts map to
504; malformed/truncated upstream streams remain 502. No estimated token usage
is substituted to make an interrupted stream appear successful.

## Recognized but unrepresentable semantics

`UnsupportedFeatureError` (a `ConversionError` subclass) separates selected
known wire features from malformed output. It currently covers refusal,
content-filter termination, matched Messages stop-sequence termination and an
unsupported translated Chat stream-usage policy. These remain STRICT rejections,
not newly supported refusal/stop mappings or permission to bypass filtering.

For translated JSON output, or SSE before visible payload, the gateway returns
422 / `unsupported_feature` / `x-should-retry: false`. Once SSE headers/payload
are committed, it emits a protocol-correct error without a successful terminal.
`UnsupportedStreamFeatureError` is the corresponding `StreamError` subclass.
Messages must retain its own semantics; refusal text is not flattened into an
ordinary answer. Error messages for these categories never contain refusal text
or the matched private stop sequence. Reasoning keeps its existing separate
`unsupported_reasoning` code and strict policy.

Unknown statuses, malformed refusal field types, contradictory snapshots and
truncated streams remain invalid upstream responses (502), not automatically
recognized features. Native protocol forwarding remains unchanged. This is an
incremental taxonomy: not every older unsupported-field rejection has been
migrated to the new class. Do not infer semantics by matching exception messages.

`--max-concurrent-requests` (Python: `max_concurrent_requests`) defaults to 16
active authenticated requests per gateway. A request or connection that finds
no free slot waits up to 0.5 s for one (a client that sends its next request as
soon as a response ends would otherwise race the slot release), then receives
503 with `Retry-After: 1` without reaching upstream. Slots are released on
completion, failure or disconnect. This is local concurrency control, not a provider RPM/TPM
rate limiter and not a global limit across gateway processes.

`--timeout` (Python: `timeout`) defaults to 300 seconds and bounds socket I/O,
including client body reads. It is **not a total generation or semantic-progress
deadline**. Incomplete client body reads time out with 408. Limits must be finite
and positive; no timeout, sandbox, privilege or real user installation is altered
by the test harnesses.

Separate upstream budgets are now available: `--total-timeout` (3600 seconds)
and `--progress-timeout` (300 seconds). The former never resets; the latter
resets for content/tool-argument progress, not pings, usage or repeated function
metadata. Deadlines cover header/body reads in the default HTTP(S) transport,
including slow-drip headers/JSON bodies, HTTP chunk-size lines, chunk terminators
and trailers. OS-rounded deadline timeouts retain non-retryable deadline codes;
a shorter socket I/O timeout keeps its transport classification. Connection establishment uses the
smaller remaining socket budget; OS DNS resolution is not forcibly cancellable.
Custom injected transports must implement bounded I/O themselves; checks around
their reads cannot interrupt arbitrary blocking Python callbacks.

The defaults are deliberately no stricter than the clients they serve: Codex
CLI's per-provider `stream_idle_timeout_ms` and Claude Code's streaming body idle
timeout are both five minutes, and neither bounds a whole stream. Hidden
reasoning can stay silent for minutes before the first visible delta, and long
outputs on slow providers can exceed ten minutes. The total budget stays finite;
shared-admission leases therefore last `total_timeout + timeout + 5` seconds.

Native routes (same protocol in and out) forward an upstream-reported stream
failure verbatim instead of replacing it with a gateway error: a Messages
`error` event, a Responses `error` or `response.failed` event, or a Chat chunk
with an `error` object. Clients keep the provider's own classification, such as
`overloaded_error`, `rate_limit_error` or `context_length_exceeded`. Only that
protocol's own epilogue may follow (Responses `response.failed` after `error`,
Chat `[DONE]`); anything else is rejected as an invalid stream. Translated routes
cannot carry the upstream error envelope verbatim, so they report the provider's
bounded, secret-redacted message with the provider's status (see
[reasoning](reasoning.md#provider-errors-inside-a-stream)); malformed streams remain
`invalid_stream`.

### Chat stream variants that translated routes reject

These are deliberate strict-contract failures, observed or documented for some
OpenAI-compatible servers. Use a native route or a provider that follows the
OpenAI streaming contract, or choose that provider's Responses endpoint:

| Variant | Result |
| --- | --- |
| First chunk with `object: ""`, empty `choices` and `prompt_filter_results` (Azure OpenAI content-filter preamble) | `invalid_stream` ("invalid Chat chunk object") |
| `finish_reason` other than `stop`, `tool_calls`, `length` (e.g. `eos`); `content_filter` | `invalid_stream`; `content_filter` is `policy_refusal` |
| No terminal `[DONE]`, or no final usage when `stream_options.include_usage` is requested for a Messages client | `invalid_stream` ("stream ended before its terminal event"); Messages clients need measured final usage |
| `function_call`/`audio` with a non-null value | `invalid_stream`; explicit `null` is treated as omitted |

Live route-doctor checks (`--allow-live`) record the resulting outcome, so the
provider/protocol choice can be changed before regular use.

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
