# Optional context preflight

The default `context_preflight="off"` preserves existing behavior. Opt-in modes:

- `estimate`: report an assessment in `X-Protoverta-Context-*` response headers;
  never reject merely because an estimate is over the configured limit.
- `reject_estimate`: explicitly reject unknown or over-estimate requests before
  upstream I/O. This conservative operator choice can reject requests that would
  actually fit; estimates are not an exact tokenizer or a proven upper bound.
- `exact`: embedding-only, requires an operator `token_counter` callback returning
  `TokenCount(..., exact=True)`. No exact counter is bundled or enabled by CLI.

Without a callback the heuristic counts UTF-8 bytes in the complete outbound JSON,
including system/history/tool schemas. It is labelled `utf8-json-byte-estimate`,
never provider-exact. Media inputs (images, audio, video, files/documents, including
tool-result content) have unknown input size. A known verdict also requires an
explicit output reserve and a model-specific `context_window_tokens` in the exact
upstream capability record. Catalog metadata alone does not supply that evidence.

Headers distinguish `within-estimate`, `over-estimate`, `within-exact`,
`over-exact`, and `unknown`, plus count kind (`X-Protoverta-Context-Count-Kind`),
high-level count category (`X-Protoverta-Context-Count-Category`: `exact`,
`estimate`, or `unavailable`), and known numerical components. Setup/selection
status (`selection_status` and launcher `model_status`) also surfaces
`count_category` (`exact`, `estimate`, or `unavailable`) and exact-record
`context_window_tokens` alongside route evidence.
Rejected preflight requests return 422; neither request history nor output budget
is rewritten, and no tokenizer download or remote count endpoint is called.
These checks do not reserve provider tokens, enforce TPM or predict billing.

The separate opt-in HTTP counting endpoints and model-bound local plugin are
documented in [counting contracts](counting-caching-media.md). Merely enabling
those endpoints does not silently enable a remote counter for every generation
preflight; embedding callers must explicitly select their counter callback.

Trusted callback code receives a deep copy and must count the full model-specific
input, tools and multimodal overhead. It owns its model/tokenizer correctness and
its own timeouts/resource limits. The gateway does not sandbox or forcibly cancel
callbacks. Callback error details are suppressed. `exact=True` is an operator
assertion, not independent certification by Protoverta.

Both launcher previews expose `context_preflight` in advanced gateway runtime
settings; only `off`, `estimate`, `reject_estimate` are serializable there.

## Local soak

`python scripts/soak_local.py --seconds 60 --concurrency 4` runs two independent
loopback gateways, synthetic credentials/models, JSON and SSE traffic, deliberate
stream truncation and client cancellation with a temporary shared-admission DB.
It checks response closure, terminal-event honesty, reply/session isolation and
client-lifetime-thread drain. `--installed` uses the installed package for wheel
validation. Bounds are 600 seconds, 15,000 requests, 16 workers, with a minimum
10 ms interval. Reports contain counters and exception class names, no payloads.
This is a bounded local resource/churn test, not an RSS measurement, production
load certification, vendor soak or proof of hour-long memory stability.
