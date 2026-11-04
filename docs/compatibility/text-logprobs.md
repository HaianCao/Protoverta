# Text token probabilities: Chat and Responses

The gateway automatically enables lossless text-logprob translation when the
client explicitly requests it. No new setup-page setting is required:

- Chat: `logprobs=true`, with optional `top_logprobs` from 0 to 20.
- Responses: `include=["message.output_text.logprobs"]`, with optional top-k.
- Omitted top-k in enabled mode is explicitly sent as zero: selected-token
  probabilities without alternatives. Zero does not disable selected-token
  probabilities when enable/include is present.
- Positive top-k without the corresponding enable/include is rejected.

Enabled requests crossing Messages reject before provider I/O: there is no
equivalent output. Disabled controls remain supported under
[generation controls](generation-controls.md). Native passthrough is unchanged.

## Preserved data and strict boundaries

Selected tokens and alternatives retain exact `token`, `bytes`, `logprob` and
candidate order. No tokenizer, inferred bytes, score calculation or normalization
is introduced. Scores must be finite, nonpositive, representable numbers; bytes
must be integer arrays in `[0,255]`. Null/missing Chat bytes cannot map losslessly
and are rejected, as are unknown record fields and unsupported refusal data.

Concatenated selected-token bytes must equal UTF-8 output text. Token labels
need not individually decode to their bytes: Unicode can span tokens. Streaming
checks cumulative alignment and each Responses text item, rather than treating
transport fragments as token boundaries. All done/content/item/terminal records
must agree with the deltas and are independently type-validated before comparison.
Float bytes or boolean scores cannot pass via Python's numeric equality.

Alternatives cannot exceed the applied top-k. Requested measurements missing
from nonempty text fail instead of becoming guessed scores or silent success.
Tool-only/empty-text output does not require nonexistent text probabilities.
Malformed, mismatched or interrupted streams never receive fabricated success;
late errors cannot retract already forwarded content.

This covers the existing synchronous text/function JSON/SSE subset. Refusal,
annotation, multimodal and opaque reasoning probabilities remain unsupported.
Model support is separate; capability feature `text_logprobs` participates in
existing negative/verified-route preflight.

## Bare library APIs

Bare conversion remains strict by default. Enable the contract explicitly:

```python
from protoverta import convert_request, convert_response, StreamTranscoder

request = {
    "model": "example-model", "input": "Hello",
    "include": ["message.output_text.logprobs"], "top_logprobs": 2,
}
wire = convert_request(request, "responses", "chat", text_logprobs=True)
# Send wire with your transport; then convert its response:
result = convert_response(
    chat_response, "chat", "responses", text_logprobs=True,
    request_context=request, max_top_logprobs=wire["top_logprobs"],
)
stream = StreamTranscoder(
    "chat", "responses", text_logprobs=True, request_context=request,
)
```

The stream derives its top-k bound from context; an explicit
`max_top_logprobs` is also supported. With no request bound, bare output permits
at most 20 alternatives per token, not proof of compliance with an unknown
request. `finish()` remains mandatory at upstream EOF.

The official baseline omits these controls. Supporting requested probabilities
does not fill omitted fields or establish full-schema compliance. See
[validation](../development/text-logprobs-2026-11-04.md).

Primary sources checked 2026-11-04:
[Chat API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/retrieve),
and pinned official OpenResponses token/alternative schemas from the
[independent audit](../development/openresponses-audit.md).
