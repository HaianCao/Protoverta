# Generated Responses request metadata

`parallel_tool_calls` describes permission to run calls in parallel, not the
number of calls observed. A completed response alone does not reveal the tools
offered to the model or the request's tool-choice policy.

Translated JSON and SSE snapshots therefore omit `parallel_tool_calls`, `tools`
and `tool_choice` when that request context is unknown. Missing/null context
values stay unknown; explicit `false`, `[]` and `"none"` are preserved. The
converter does not fill the gap with guessed defaults or null placeholders for
nonnullable schema fields. Native Responses passthrough remains unchanged.

The gateway restores explicitly supplied fields from the original validated
Responses request. Omitting a field in that request does not establish an
upstream default. No global/session policy is inferred from a previous request.

Standalone callers can supply the same original request:

```python
from protoverta import convert_request, convert_response, StreamTranscoder

request = {
    "model": "example-model",
    "input": "Hello",
    "tools": [],
    "tool_choice": "none",
    "parallel_tool_calls": False,
}
upstream_request = convert_request(request, "responses", "chat")
# Send upstream_request to the chosen Chat provider yourself.
response = convert_response(
    chat_response, "chat", "responses", request_context=request
)
stream = StreamTranscoder("chat", "responses", request_context=request)
```

`request_context` is only supported for translated Responses output. It accepts
the full original request or its supported metadata projection. Metadata types
and JSON validity are checked; this does not replace full request validation,
verify that a response belongs to that request, or prove the upstream obeyed
every requested setting. The supported projection contains `instructions`,
`max_output_tokens`, `tools`, `tool_choice`, `parallel_tool_calls`, `text`,
`temperature` and `top_p`. Unsupported request fields are not projected.

The stream constructor freezes the projection before consuming bytes. Mutating
the caller's request later cannot change created/completed snapshots. For an
existing custom transformation pipeline, `bind_response_context(request)` is an
exported callback factory; it retains only supported metadata, not the request
input. When used with `response_transform`, custom name restoration runs first
and the explicit request metadata is applied afterwards.

This corrects an alpha contract: older standalone output guessed empty tools,
automatic tool choice and a parallel-call flag. Consumers requiring a complete
Responses schema must supply known request metadata or use native Responses.
Other schema/usage gaps remain separately tracked; no missing usage subtotal,
sampling default, reasoning signature or service tier is invented.

Within explicitly supplied function tools, omitted `description`, `parameters`
and `strict` are projected as nullable unknown values in response metadata.
Explicit `""`, `{}` and `false` are preserved. This normalization changes only
the copied response snapshot, not the original or upstream request, and does not
enable strict tool execution. Generated SSE also includes the same nullable
`max_tool_calls`, `safety_identifier` and `prompt_cache_key` envelope as JSON.
These additions do not establish full OpenResponses conformance; see the
[independent schema audit](../development/openresponses-audit.md).

The pinned strict SDK gate uses explicit policy in its positive Responses
fixtures. Separate negative cases verify that omitted policy is rejected at
`response.created`; that failure is not presented as successful compatibility.

Reference: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling).
