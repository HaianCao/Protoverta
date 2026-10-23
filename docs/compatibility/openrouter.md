# OpenRouter conformance notes

Verified on 2026-10-23 using only synthetic prompts and `add_numbers(a=2, b=3)`.
Model: `nvidia/nemotron-3-ultra-550b-a55b:free`.
API prefix: `https://openrouter.ai/api/v1`.

All six cross-protocol directions completed JSON text conversion and a streamed
function call, a matching tool result, and the final text marker. Messages output used the explicit
deferred-usage policy. Test requests explicitly disabled upstream reasoning:
`reasoning: {enabled: false}` for Chat/Responses, or
`thinking: {type: "disabled"}` for Messages. These provider-specific controls were
originally applied by the test transport. The production gateway now exposes
the same explicit control as `upstream_reasoning="openrouter_disabled"` (or
`--upstream-reasoning openrouter_disabled`); it remains opt-in.

Default reasoning is enabled on this model. Native Messages/Responses can carry
its thinking/reasoning payloads unchanged; converting those payloads is outside
the current subset and is deliberately rejected. A successful text/tool probe
with reasoning disabled does not prove default Claude Code/Codex compatibility
or hidden-state resume.

Actual CLI verification on the same date used Codex CLI 0.163.0 and Claude Code
2.1.308 through each consumer launcher from source, with synthetic text prompts
and temporary client/config directories. All six client/upstream combinations
(Codex and Claude, each with Chat, Responses and Messages) returned the expected
marker and exit code zero using the production `openrouter_disabled` policy.
No transport injection was used. This verifies fresh-session text round trips,
not cross-provider resume. The earlier six-direction function-call probes remain
separate protocol-level checks.

Follow-up real CLI tool checks passed in all four cross-protocol routes:
Claude → Chat/Responses read a random nonce from a temporary file and returned it
in the final answer. Codex → Chat/Messages used the read-only `get_goal` tool;
the real transport observed its matching call/result ID in the second upstream
request, followed by the final marker and exit code zero. A first attempt at
Codex shell file reads was blocked by local execution policy, so no shell policy
was weakened. Native real-provider tool routes and resume are not covered by
these four results.

The real Codex → Chat loop exposed whitespace-only assistant placeholders after
a function call. The explicit Codex profile omits only these validated request
placeholders; meaningful text, annotations and unknown fields still fail.
Standalone strict conversion is unchanged. Probe instrumentation observes the
production transport and does not rewrite provider requests or responses.

Both preview setup pages expose **Upstream reasoning → Disabled — OpenRouter**
and persist it per provider. Gateway mode is required, including native routes;
direct mode is not silently modified. The saved policy overrides selected effort.
Existing configurations keep `provider_default` and their previous behavior.

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

Follow-up text resume also passed for Codex Chat → Messages and Claude
Chat → Responses on the same model, with explicit upstream reasoning disabled.
The tests retained the session ID and recalled a synthetic token from the prior
turn. See [resume checks and remaining boundaries](resume.md); these results do
not establish real-provider resume after tool calls or changing real models.

References: [reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
[model](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free).
