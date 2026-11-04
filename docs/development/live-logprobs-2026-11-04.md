# Bounded live logprob checks

Checkpoint `af89a98` was committed before these checks. The user selected local
commits/tests only; no branch was pushed and remote CI is not executed.

Four single-attempt requests were made to the previously approved free models,
with a synthetic "Reply with exactly OK" prompt and a 64-token output cap.
Each requested selected-token probabilities and two alternatives. No response,
reasoning, token values or credentials are stored. Streaming was not attempted
after failed JSON checks. The initial obsolete-interpreter local rejection was
not provider evidence; the report uses the validated installed logprob wheel.

| Provider/model | Diagnostic observation | Verdict |
|---|---|---|
| OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` | Text present, normal stop, no logprob records despite the request | Not certified; gateway rejects missing requested measurements |
| NVIDIA NIM `z-ai/glm-5.3-flash` | 64 logprob records with bytes, reasoning present, length limit, no final text | Not certified for text-logprob translation under strict reasoning |

These observations concern these exact calls, not universal claims about a
model or provider. The NIM result cannot establish that reasoning probabilities
are valid final-text probabilities. No thinking-off capability, signature or
text answer was invented. Request caps were not raised and no new model/key or
paid fallback was tried. No further live requests are scheduled by this record.

The probe exercises a real Chat endpoint, gateway-generated Responses output,
and local reverse conversion. It does not certify a provider's own Responses
endpoint. The script reports local requests and counted upstream attempts
separately, fixed error codes/phases and structural counts only. Deadlines,
wire limits, redirect blocking and a single-attempt policy remain active.

`scripts/probe_logprobs.py` requires `--allow-live` and an existing key environment
variable. Eight synthetic tests cover JSON/SSE conversion, 400/401/404/429/503
without retries, attempt accounting and payload-free shape diagnostics.
Machine-readable results: [live report](live-logprobs-2026-11-04.json).
Successful synthetic/SDK tests remain separate from this negative live gate.
