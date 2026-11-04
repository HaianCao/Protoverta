# Token counting, caching and extended content contracts

## Counting

Authenticated loopback endpoints:

- `POST /v1/messages/count_tokens`
- `POST /v1/responses/input_tokens`

`token_count_mode` defaults to `off` (422 when requested). `estimate` explicitly
returns the complete JSON byte heuristic with `X-Protoverta-Count-Kind:
utf8-json-byte-estimate` and `X-Protoverta-Count-Category: estimate`; media
without a counter rejects. An SDK may ignore these headers, so operators must
not enable estimates expecting native-exact semantics. Exact counts (`native` or
`operator-exact`) emit `X-Protoverta-Count-Category: exact`.

`native` calls only the configured Messages `/messages/count_tokens` or Responses
`/responses/input_tokens` endpoint, preserving the exact API prefix and auth style.
The result must contain a nonnegative integer; Responses also requires
`object: "response.input_tokens"`. Provenance is `provider-native`, which reports
what the provider counted, not a third-party tokenizer certification. Chat has no
assumed native counting API; it rejects instead of generating an answer or guessing
a different endpoint. Cross-protocol count requests use the strict converter.
They do not apply the client compatibility profile's telemetry/cache stripping.

Native calls refuse redirects, close responses/errors, use bounded read deadlines
(at most 30 seconds), and limit count responses to 64 KiB. They do not retry,
fall back to generation, or automatically switch to estimates. They share the
configured cross-process provider admission budget. No count result is cached.

Embedding-only `operator` mode requires `token_counter`. `ModelTokenCounter`
provides a model/protocol-bound local plugin with explicit complete input renderer
and encoder callables. Nothing is dynamically imported/downloaded. It rejects
media and mismatched models/protocols; exactness must be asserted by an operator
who has verified the model template (including system/tools/history). Labels are
operator-exact or operator-estimate. Callback implementation/runtime is trusted
and must bound itself; exceptions are sanitized. Preflight can use the same
callback separately. There is no universal bundled exact tokenizer.

Official endpoint contracts:
[Responses token counts](https://developers.openai.com/api/docs/guides/token-counting),
[Messages token counts](https://platform.claude.com/docs/en/build-with-claude/token-counting).

## Certified prompt cache checkpoints

The default `prompt_caching="off"` preserves existing compatibility behavior.
Native forwarding preserves native cache hints as before. Optional `ephemeral`
supports one precisely scoped translated contract: a Messages initial system
text checkpoint → Chat-compatible upstream that explicitly supports the same
Anthropic ephemeral end-of-system breakpoint, including the requested TTL.

It requires an exact provider/model profile with
`prompt_cache_semantics: "anthropic_ephemeral_system_end"`. Only the final system
text block may carry `{type:"ephemeral", ttl:"5m"|"1h"}` (ttl may be omitted).
The ordered system text becomes one Chat system text part with the same endpoint
checkpoint and hint. Intermediate, user, tool and unsupported markers reject under
this opt-in policy; they are never silently relocated or stripped. No provider
is granted this profile automatically. Profiles must cite actual provider support,
including TTL semantics. Generic Chat compatibility is insufficient evidence.

Responses cache keys, retention settings and provider-specific automatic caching
are not substituted for an Anthropic breakpoint. A successful request does not
prove a cache hit: inspect actual measured cache usage. Accounting already maps
reported cache reads/writes; diagnostics now expose those values when measured.
This implementation does not cache generated answers, change prompts, insert new
checkpoints automatically, or run a global cross-session response cache.

ClaudApter's existing cross-protocol CLI compatibility settings still disable
Claude Code's automatic caching: that client can place additional checkpoints
outside this certified subset. Selecting this gateway policy does not silently
re-enable the client's broader cache behavior. The policy is usable by API
callers sending the supported explicit system checkpoint. Native Messages caching
and provider-managed automatic caching remain separate. Broader client checkpoint
translation requires its own verified contract before changing that launcher flag.

## Content and state matrix

| Feature | Portable conversion | Native route / reason for boundary |
|---|---|---|
| Inline base64 PDF user input | All six request directions, same bytes/order | Exact application/pdf data URL or Messages base64 source; header validated, provider parses content |
| PDF filename | Chat ↔ Responses | Messages has no equivalent filename; title is not substituted |
| PDF URL / uploaded file ID | Rejected across protocols | Native forwarding remains provider-owned; gateway never fetches/uploads files |
| PDF title/context/citations | Rejected across protocols | No silent loss or conversion of citations into plain text |
| PDF in tool results or generated PDF output | Rejected | Not silently turned into an empty text result or moved to a user role |
| Audio/video | Rejected across these portable routes | Native compatible endpoint contract remains separate; no invented transcript/audio/image substitution |
| Hosted web/file search, computer use | Rejected as portable function tools | Native provider tool lifecycle can be forwarded; adapter does not execute hosted tools |
| Responses compaction | Native `POST /v1/responses/compact` only | Non-streaming native forwarding with normal auth, resource limits and optional capability checks |
| Compaction/encrypted items targeting Chat/Messages | Rejected | Opaque state cannot be reconstructed as text or signed thinking |
| Custom tool JSON | Existing Chat ↔ Responses mapping | Messages grammar semantics and undocumented Chat custom SSE remain rejected |

PDF base64 length is bounded to 8 MiB (gateway JSON body limits also apply).
Only ordinary user content can carry portable PDFs; input ordering is retained.
`documents`, `compaction`, `audio`, `video` and `hosted_tools` are independent capability features; a text/image
test does not certify them. No new provider was certified by local fixtures.
Audio, hosted tools and opaque compaction do not have equivalent semantics merely
because an endpoint accepts text or returns HTTP 200. Their rejection is part of
the implemented contract, not a claim they were emulated successfully.

Native compaction does not run generation reasoning overrides or context-budget
rewrites. It does not persist/merge sessions or invoke compaction automatically.
The returned opaque object belongs to its original provider/resource.

References: [OpenAI file inputs](https://developers.openai.com/api/docs/guides/file-inputs),
[Chat audio](https://developers.openai.com/api/docs/guides/audio-chat-completions),
[Responses compaction](https://developers.openai.com/api/docs/guides/compaction).
