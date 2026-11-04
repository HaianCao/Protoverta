# Penalties and disabled logprobs

These mappings are deliberately route-specific. They do not establish a common
generation-control contract for every model or all three protocols.

| Explicit input | Target | Behavior |
|---|---|---|
| OpenResponses `frequency_penalty` / `presence_penalty` | Chat | Forward the exact finite value in `[-2, 2]`, including zero |
| Chat penalties | Responses | Reject by default; forward only under an explicit matching Responses penalty contract |
| Chat penalties | Messages | Reject; no equivalent target control is enabled |
| OpenResponses penalties | Messages | Reject, including zero; do not emulate by changing instructions |
| Chat `logprobs=false` or `top_logprobs=0` | Responses | Send `top_logprobs=0` |
| Responses `top_logprobs=0` | Chat | Send `logprobs=false`; omit top-k because no probabilities are requested |
| Explicit disabled logprobs from Chat/Responses | Messages | No probability-output field; strict response decoding still rejects actual probability payloads |
| Chat `logprobs=true`, or Responses probability include | Chat/Responses | Gateway enables [text-logprob translation](text-logprobs.md); bare APIs require `text_logprobs=True` |
| Enabled probabilities | Messages | Reject before I/O; no equivalent output |
| Positive top-k without enable/include | Any translated target | Reject the incomplete control combination |
| Messages input carrying these controls | Chat/Responses | Reject; they are not native Messages controls |

OpenResponses penalties are an incoming gateway dialect extension from its
pinned creation schema. Accepting them does not mean the OpenAI Responses API
or an arbitrary Responses provider accepts them as outgoing controls. Protoverta
therefore enables penalty forwarding by default only when the target is Chat.
An exact operator-declared [generation profile](generation-profiles.md) can
enable a Responses provider dialect with equivalent penalty semantics. This is
not a declaration about the standard OpenAI Responses API. Bare conversion APIs
require `responses_penalties=True` and a Responses target for that opt-in.
Provider/model support remains separate; capability evidence can reject an explicitly
unsupported control, and verified-route policy still requires matching evidence.

Types/ranges are checked before sending. Booleans are not numeric penalties or
top-k values. Nonfinite numbers, malformed flags, negative top-k, and top-k above
20 fail. `logprobs=true, top_logprobs=0` still requests selected-token logprobs,
so it requires enabled text-logprob support. Null/missing controls
remain absent rather than becoming fabricated defaults.

Generated Responses JSON/SSE snapshots preserve explicit penalties and zero
top-k from the validated request. The gateway has sent the corresponding
outgoing controls, and strict decoding does not discard nonempty probability
data. A bare `request_context` projection describes supplied metadata only;
callers must validate/send the corresponding request themselves.

No probability values are inferred from generated text, and no default penalty
or top-k is invented. Native passthrough is unchanged. Consequently these
features do not remove the baseline schema findings for omitted controls.

Sources checked 2026-11-04:
[official Chat API controls](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[OpenResponses specification](https://www.openresponses.org/specification), and
the audit's unchanged pinned `openresponses@8907bc3b7c4300ff5c21fd647cfde9929243afa1`
creation/resource schemas. Text-logprob translation now checks token/byte
alignment and streaming snapshots within its documented subset.
