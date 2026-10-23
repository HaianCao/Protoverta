# OpenRouter conformance notes

Verified on 2026-10-23 using only synthetic prompts and `add_numbers(a=2, b=3)`.
Model: `nvidia/nemotron-3-ultra-550b-a55b:free`.
API prefix: `https://openrouter.ai/api/v1`.

All six cross-protocol directions completed JSON text conversion and a streamed
function call, a matching tool result, and the final text marker. Messages output used the explicit
deferred-usage policy. Test requests explicitly disabled upstream reasoning:
`reasoning: {enabled: false}` for Chat/Responses, or
`thinking: {type: "disabled"}` for Messages. These provider-specific controls were
applied by the test transport, not silently added by the production gateway.

Default reasoning is enabled on this model. Native Messages/Responses can carry
its thinking/reasoning payloads unchanged; converting those payloads is outside
the current subset and is deliberately rejected. A successful text/tool probe
with reasoning disabled does not prove default Claude Code/Codex compatibility
or hidden-state resume. The consumer launchers must expose or propagate suitable
upstream controls before this model is advertised as working out of the box.

Transport compatibility covered by synthetic regression tests:

- Chat may repeat its finished, empty choice in a final usage postlude. Only one
  unchanged finish with no new content and first-time usage qualifies.
- Responses may omit named SSE events and use the JSON `type` discriminator.
  Explicit conflicting event names still fail.
- Responses may append one `[DONE]` after its real terminal event. Early or
  repeated sentinels, later payloads and interrupted streams still fail.
- Real input/output usage, matching tool IDs/arguments and clean EOF are required.
- Empty/null JSON reasoning and citation placeholders are accepted; meaningful
  reasoning or citation content still fails rather than being discarded.

No request headers, credentials or user conversations are committed as fixtures.
These results describe one provider/model at one time, not universal support.

References: [reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
[model](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free).
